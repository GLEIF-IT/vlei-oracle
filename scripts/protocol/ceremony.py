"""Finalize a proof-bearing association for issuance with KLI.

Nonce consumption is fail closed: a crash after consumption burns that nonce.
Inspect KLI state after interrupted issuance and resume its KERIpy escrows;
never create a second credential with its nonce. This command does not hold issuer keys.
"""

from __future__ import annotations

import argparse
import json
import lmdb
from pathlib import Path

from keri import kering
from keri.core import coring, serdering
from keri.kering import Protocols, versify

from vlei_oracle.noncing import NonceLedger
from vlei_oracle.association.constants import LE_SCHEMA
from vlei_oracle.association.eoa import (
    DOMAIN_TAG,
    PROOF_ALGORITHM,
    build_challenge,
    verify_eoa_proof,
)
from vlei_oracle.association.schemas import association_schemer


def association_from_challenge(challenge, signature, registry):
    """Assemble schema-specific blocks with KERIpy SAID/ACDC APIs."""
    fields = challenge["message"]
    expected = build_challenge(
        issuer_aid=fields["issuerAid"],
        le_vlei_said=fields["leVleiSaid"],
        ext_id=fields["extId"],
        nonce=fields["nonce"],
    )
    if challenge != expected:
        raise ValueError("challenge does not match the EOA profile")
    if coring.Saider(qb64=registry).qb64 != registry:
        raise ValueError("registry must be a canonical KERI SAID")
    proof = coring.Saider.saidify(
        sad={
            "d": "",
            "alg": PROOF_ALGORITHM,
            "tag": DOMAIN_TAG,
            "sig": signature,
        }
    )[1]
    attributes = coring.Saider.saidify(
        sad={
            "d": "",
            "extId": fields["extId"],
            "proof": proof,
        }
    )[1]
    edge = coring.Saider.saidify(
        sad={
            "d": "",
            "le": {"n": fields["leVleiSaid"], "s": LE_SCHEMA, "o": "I2I"},
        }
    )[1]
    return serdering.SerderACDC(
        sad={
            "v": versify(protocol=Protocols.acdc, size=0),
            "d": "",
            "u": fields["nonce"],
            "i": fields["issuerAid"],
            "ri": registry,
            "s": association_schemer().said,
            "a": attributes,
            "e": edge,
        },
        makify=True,
    )


def write_credential(*, challenge, signature, registry, nonce_ledger, output):
    """Verify the proof, consume the issuer's exact nonce, then expose the ACDC.

    Borrow an open NonceLedger; it owns only durable consumption, scoped by issuer and exact nonce.
    Credential bytes and proof remain owned by the output and KERI credential store.
    """
    creder = association_from_challenge(challenge, signature, registry)
    association_schemer().verify(creder.raw)
    verify_eoa_proof(creder.sad)
    output = Path(output)
    # Exclusive creation avoids overwriting an operator's existing artifact.
    with output.open("xb") as stream:
        try:
            if not nonce_ledger.claim(issuer=creder.issuer, nonce=creder.sad["u"]):
                raise ValueError("nonce already consumed by this issuer")
            # Consumption is committed before another process can read usable bytes.
            stream.write(creder.raw)
            stream.flush()
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    return creder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument(
        "--signature",
        type=Path,
        required=True,
        help="65-byte 0x hexadecimal signature file",
    )
    parser.add_argument("--registry", required=True)
    parser.add_argument(
        "--nonce-dir",
        type=Path,
        required=True,
        help="LMDB issuer nonce directory; reuse across ceremonies",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        with NonceLedger(args.nonce_dir) as ledger:
            creder = write_credential(
                challenge=json.loads(args.challenge.read_text()),
                signature=args.signature.read_text().strip(),
                registry=args.registry,
                nonce_ledger=ledger,
                output=args.out,
            )
    except (ValueError, KeyError, TypeError, OSError, lmdb.Error, kering.KeriError) as exc:
        parser.exit(1, f"ceremony: {exc}\n")
    print(creder.said)


if __name__ == "__main__":
    main()

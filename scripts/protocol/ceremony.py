"""Finalize a proof-bearing association for issuance with KLI.

Nonce consumption is fail closed: a crash after consumption burns that nonce.
Inspect KLI state after interrupted issuance and resume its native escrows;
never create a second credential with its nonce. This command does not hold issuer keys.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from keri import kering
from keri.core import coring, serdering
from keri.kering import Protocols, versify

from vlei_oracle.constants import LE_SCHEMA
from vlei_oracle.eoa import (
    DOMAIN_TAG,
    PROOF_ALGORITHM,
    build_challenge,
    verify_eoa_proof,
)
from vlei_oracle.schema_cache import association_schemer


def association_from_challenge(challenge, signature, registry):
    """Assemble schema-specific blocks with native KERIpy SAID/ACDC APIs."""
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


def consume_nonce(nonce_store, *, issuer, nonce, association_said):
    """Commit exact issuer-scoped consumption before credential publication."""
    with closing(sqlite3.connect(nonce_store, timeout=0.0)) as ledger, ledger:
        # Acquire the writer reservation before any schema/read lock. With a
        # deferred transaction, two initializers can otherwise block each other's
        # lock upgrade and both fail. Contenders still fail without waiting.
        ledger.execute("BEGIN IMMEDIATE")
        ledger.execute(
            "CREATE TABLE IF NOT EXISTS consumed (issuer TEXT, nonce TEXT, association TEXT, PRIMARY KEY (issuer, nonce))"
        )
        try:
            ledger.execute(
                "INSERT INTO consumed VALUES (?, ?, ?)",
                (issuer, nonce, association_said),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("nonce already consumed by this issuer") from exc


def write_credential(*, challenge, signature, registry, nonce_store, output):
    """Verify the proof, consume the issuer's exact nonce, then expose the ACDC.

    The ledger owns only durable consumption, scoped by issuer and exact nonce.
    Credential bytes and proof remain owned by the output and KERI credential store.
    """
    creder = association_from_challenge(challenge, signature, registry)
    association_schemer().verify(creder.raw)
    verify_eoa_proof(creder.sad)
    nonce_store, output = Path(nonce_store), Path(output)
    nonce_store.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting an operator's existing artifact.
    with output.open("xb") as stream:
        try:
            consume_nonce(nonce_store, issuer=creder.issuer,
                          nonce=creder.sad["u"], association_said=creder.said)
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
        "--nonce-store",
        type=Path,
        required=True,
        help="Durable issuer nonce ledger; reuse across ceremonies",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        creder = write_credential(
            challenge=json.loads(args.challenge.read_text()),
            signature=args.signature.read_text().strip(),
            registry=args.registry,
            nonce_store=args.nonce_store,
            output=args.out,
        )
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, kering.KeriError) as exc:
        parser.exit(1, f"ceremony: {exc}\n")
    print(creder.said)


if __name__ == "__main__":
    main()

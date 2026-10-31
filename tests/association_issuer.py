"""Test-owned ACDC assembly; not an oracle command or issuance policy."""

from keri.core import coring, serdering
from keri.kering import Protocols, versify
from eoa_verifier.verification.constants import ASSOCIATION_SCHEMA, LE_SCHEMA
from eoa_verifier.verification.eoa import DOMAIN_TAG, PROOF_ALGORITHM, build_challenge


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
            "s": ASSOCIATION_SCHEMA,
            "a": attributes,
            "e": edge,
        },
        makify=True,
    )

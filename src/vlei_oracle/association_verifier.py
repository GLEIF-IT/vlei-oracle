"""Verify received association credentials against accepted KERI state."""

from keri.core import coring, serdering
from keri.vdr import credentialing

from .constants import LE_SCHEMA, QVI_SCHEMA
from .keri_store import MissingCredentialArtifactsError
from .verifier import VerificationError


def verify_association(
    *,
    rgy: credentialing.Regery,
    said: str,
    schema_said: str,
    trusted_geda_aid: str,
) -> serdering.SerderACDC:
    """Check current LE/QVI trust and return the accepted association ACDC.

    Missing accepted credentials or TEL state raise MissingCredentialArtifactsError
    for retry. Invalid relationships and revoked credentials raise VerificationError.
    External-profile verification is a separate step owned by the coordinator.
    """
    association = accepted_credential(rgy, said, schema_said)
    if "i" in association.attrib:
        raise VerificationError("association must be untargeted")
    le_edge = association.edge["le"]
    if le_edge.get("s") != LE_SCHEMA or le_edge.get("o") != "I2I":
        raise VerificationError("association must reference its LE credential with I2I")
    le = accepted_credential(rgy, le_edge["n"], LE_SCHEMA)
    qvi_edge = le.edge["qvi"]
    if qvi_edge.get("s") != QVI_SCHEMA:
        raise VerificationError("LE edge must reference a QVI credential")
    qvi = accepted_credential(rgy, qvi_edge["n"], QVI_SCHEMA)
    if association.issuer != le.attrib["i"] or le.issuer != qvi.attrib["i"]:
        raise VerificationError(
            "credential issuer does not match source credential holder"
        )
    if qvi.issuer != trusted_geda_aid:
        raise VerificationError("QVI credential is not issued by the trusted GEDA")
    qvi_state = rgy.hby.kevers.get(le.issuer)
    if qvi_state is None or qvi_state.delpre != trusted_geda_aid:
        raise VerificationError("QVI AID is not delegated by the trusted GEDA")

    # Each nested SAID commits its own block as well as the enclosing ACDC.
    for block in (association.attrib["proof"], association.attrib, association.edge):
        if not coring.Saider(qb64=block["d"]).verify(sad=block, prefixed=True):
            raise VerificationError("association block SAID does not verify")
    return association


def accepted_credential(rgy, said: str, schema_said: str) -> serdering.SerderACDC:
    """Read an accepted credential with the expected schema and live TEL state.

    KERIpy's ``saved`` index marks credential acceptance. Re-read TEL status
    because an accepted credential may since have been revoked. Missing evidence
    is retryable; an unexpected schema, registry issuer, or revocation is invalid.
    """
    if rgy.reger.saved.get(keys=(said,)) is None:
        raise MissingCredentialArtifactsError(f"credential {said} is not accepted yet")
    creder = stored_credential(rgy, said, schema_said)
    tever = rgy.reger.tevers.get(creder.regi)
    state = tever.vcState(said) if tever is not None else None
    if state is None:
        raise MissingCredentialArtifactsError(f"credential {said} has no TEL state")
    if state.et not in ("iss", "bis"):
        raise VerificationError(f"credential {said} is revoked")
    if tever.pre != creder.issuer:
        raise VerificationError("credential registry belongs to another issuer")
    return creder


def stored_credential(rgy, said: str, schema_said: str) -> serdering.SerderACDC:
    """Read and validate credential bytes separately from acceptance/status."""
    creder, *_ = rgy.reger.cloneCred(said=said)
    creder = serdering.SerderACDC(raw=creder.raw)
    if creder.schema != schema_said:
        raise VerificationError(f"credential {said} has unexpected schema")
    return creder

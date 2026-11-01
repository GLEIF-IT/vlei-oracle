"""Decode trusted verifier status and canonical external identifiers."""

import json
import re

from eth_utils import keccak
from keri import kering
from keri.core import coring

from .records import AssociationStatus, StatusEvent


def read_status(body: bytes, *, chain_id: int):
    """Return event and status from the small v1 object, or raise ValueError.

    The caller must authenticate the exact bytes first. Only accepted associations and
    revoked status with reason REVOKED are supported. Observation metadata is ignored:
    the configured verifier already performed the initial account/chain checks.
    """
    try:
        payload = json.loads(body)
        said, status_revision = payload["associationSaid"], payload["statusRevision"]
        if (
            payload["version"] != "1"
            or type(status_revision) is not int
            or status_revision < 1
        ):
            raise ValueError("unsupported event version or revision")
        if coring.Saider(qb64=said).qb64 != said:
            raise ValueError("invalid association SAID")
        if payload["eventId"] != f"{said}:{status_revision}":
            raise ValueError("event ID must identify association and revision")
        if payload["status"] == "accepted" and "reasonCode" not in payload:
            status = "accepted"
        elif payload["status"] == "revoked" and payload.get("reasonCode") == "REVOKED":
            status = "revoked"
        else:
            raise ValueError("unsupported association status")
        facts = payload["association"]
        issuer, le, issuance = (
            facts["issuerAid"],
            facts["leSaid"],
            facts["issuanceAnchor"],
        )
        if (
            coring.Prefixer(qb64=issuer).qb64 != issuer
            or coring.Saider(qb64=le).qb64 != le
        ):
            raise ValueError("invalid issuer or LE identifier")
        anchor, sequence = issuance["said"], issuance["sequenceNumber"]
        if (
            coring.Saider(qb64=anchor).qb64 != anchor
            or type(sequence) is not int
            or sequence < 0
        ):
            raise ValueError("invalid issuance anchor")
        if ext_id_chain(facts["extId"]) != chain_id:
            raise ValueError("association belongs to another chain")
        return StatusEvent(payload["eventId"], body.decode("utf-8")), AssociationStatus(
            said,
            status_revision,
            status,
            facts,
            facts["extId"].split(":")[2],
            "0x" + keccak(text=issuer).hex(),
        )
    except (KeyError, TypeError, ValueError, kering.KeriError) as exc:
        raise ValueError(f"invalid status event: {exc}") from exc


def ext_id_chain(ext_id: str) -> int:
    """Return the chain ID from a canonical EOA CAIP-10 identifier.

    Committed identifiers must already be normalized; aliases are rejected.
    """
    if not isinstance(ext_id, str) or not re.fullmatch(
        r"eip155:[1-9][0-9]{0,31}:0x[0-9a-f]{40}", ext_id
    ):
        raise ValueError("external identifier must be canonical EOA CAIP-10")
    chain_id = int(ext_id.split(":")[1])
    if chain_id >= 2**256:
        raise ValueError("chain ID exceeds uint256")
    return chain_id

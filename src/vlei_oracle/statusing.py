"""Decode trusted verifier status without re-verifying credentials or accounts."""

import json
from dataclasses import dataclass

from eth_utils import keccak
from keri import kering
from keri.core import coring

from vlei_oracle.verification.eoa import ext_id_chain


@dataclass
class StatusEvent:
    """Immutable authenticated event bytes, retained for duplicate/conflict detection."""

    eventId: str
    body: str


@dataclass
class AssociationStatus:
    """Latest verifier status and immutable facts, with a separate local selection decision."""

    associationSaid: str
    revision: int
    status: str
    association: dict
    wallet: str
    identityRef: str
    selection: str = "unselected"  # selected, superseded, rejected or revoked after selection.


def read_status(body: bytes, *, chain_id: int):
    """Return event and status from the small v1 object, or raise ValueError.

    The caller must authenticate the exact bytes first. Only valid acceptance and
    explicit REVOKED retirement are supported. Observation metadata is ignored:
    the configured verifier already performed the initial account/chain checks.
    """
    try:
        payload = json.loads(body)
        said, revision = payload["associationSaid"], payload["revision"]
        if payload["version"] != "1" or type(revision) is not int or revision < 1:
            raise ValueError("unsupported event version or revision")
        if coring.Saider(qb64=said).qb64 != said:
            raise ValueError("invalid association SAID")
        if payload["eventId"] != f"{said}:{revision}":
            raise ValueError("event ID must identify association and revision")
        if payload["outcome"] == "valid" and "reasonCode" not in payload:
            status = "accepted"
        elif payload["outcome"] == "invalid" and payload.get("reasonCode") == "REVOKED":
            status = "revoked"
        else:
            raise ValueError("unsupported association status")
        facts = payload["association"]
        issuer, le, issuance = facts["issuerAid"], facts["leSaid"], facts["issuance"]
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
            revision,
            status,
            facts,
            facts["extId"].split(":")[2],
            "0x" + keccak(text=issuer).hex(),
        )
    except (KeyError, TypeError, ValueError, kering.KeriError) as exc:
        raise ValueError(f"invalid status event: {exc}") from exc

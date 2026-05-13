"""Read ACDC material from the oracle's local KERIpy verifier store.

The oracle receives wallet credentials and recursive source artifacts through
standard IPEX Grant traffic. This module translates the resulting KERIpy Reger
state into the explicit presentation shape consumed by ``verifier.py``. Missing
credentials are non-terminal because the parser may observe the Grant before
all chained artifacts and TEL events have been materialized locally.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from keri.core import coring
from keri.vdr import credentialing


class MissingCredentialArtifactsError(Exception):
    """Raised when the local KERI store does not yet contain a credential chain artifact."""


def presentation_from_reger(*, rgy: credentialing.Regery, said: str) -> dict[str, Any]:
    """Build a verifier presentation from an open KERIpy credential registry.

    The returned bundle includes credential SADs, original raw ACDC bytes for
    SAID verification, TEL-derived status records, and recursive source-chain
    credentials discovered through embedded edges.
    """

    credentials: dict[str, Any] = {}
    credential_raws: dict[str, str] = {}
    statuses: dict[str, Any] = {}
    revoked: list[str] = []

    def add_chain(credential_said: str) -> None:
        if credential_said in credentials:
            return

        try:
            creder, *_ = rgy.reger.cloneCred(said=credential_said)
        except Exception as exc:
            raise MissingCredentialArtifactsError(f"credential {credential_said} is not available yet") from exc
        if creder is None:
            raise MissingCredentialArtifactsError(f"credential {credential_said} is not available yet")
        credentials[creder.said] = creder.sad
        credential_raws[creder.said] = creder.raw.decode("utf-8")
        state = _credential_state(rgy, creder)
        if state is not None:
            status = asdict(state)
            statuses[creder.said] = status
            if status.get("et") in ("rev", "brv"):
                revoked.append(creder.said)

        for edge in (creder.edge or {}).values():
            if isinstance(edge, dict) and isinstance(edge.get("n"), str):
                add_chain(edge["n"])

    add_chain(said)
    return {
        "walletCredential": said,
        "credentials": credentials,
        "credentialRaws": credential_raws,
        "credentialStatuses": statuses,
        "revokedCredentials": sorted(set(revoked)),
    }


def _credential_state(rgy: credentialing.Regery, creder: Any) -> Any | None:
    """Return TEL state for a credential when its registry is available."""

    if not creder.regi or creder.regi not in rgy.reger.tevers:
        return None
    return rgy.reger.tevers[creder.regi].vcState(coring.Saider(qb64=creder.said).qb64)

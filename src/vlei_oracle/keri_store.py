"""Read verified ACDC material from a local KERIpy store."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from keri.app.cli.common import existing
from keri.core import coring
from keri.vdr import credentialing


def presentation_from_keri_store(
    *,
    name: str,
    alias: str,
    said: str,
    base: str = "",
    passcode: str | None = None,
) -> dict[str, Any]:
    """Build a verifier presentation from a KERIpy credential store.

    This intentionally reads KERIpy's verified credential cache instead of
    reparsing CLI text. The shell e2e script can still own orchestration while
    the oracle package owns the KERI/ACDC extraction model.
    """

    hby = existing.setupHby(name=name, base=base, bran=passcode)
    hby.habByName(alias)
    rgy = credentialing.Regery(hby=hby, name=name, base=base)

    credentials: dict[str, Any] = {}
    credential_raws: dict[str, str] = {}
    statuses: dict[str, Any] = {}
    revoked: list[str] = []

    def add_chain(credential_said: str) -> None:
        if credential_said in credentials:
            return

        creder, *_ = rgy.reger.cloneCred(said=credential_said)
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
    if not creder.regi or creder.regi not in rgy.reger.tevers:
        return None
    return rgy.reger.tevers[creder.regi].vcState(coring.Saider(qb64=creder.said).qb64)

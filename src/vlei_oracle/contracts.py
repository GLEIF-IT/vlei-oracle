"""Deployment-log helpers for local vlei-comply runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def parse_deploy_log(text: str) -> dict[str, str]:
    """Parse DeployAll console output into contract addresses."""

    labels = {
        "AdminControls": "adminControls",
        "Vendor IdentityRegistry": "identityRegistry",
        "CredentialRegistry": "credentialRegistry",
        "VLEIOracleRegistry": "oracleRegistry",
        "Asset (vLEI)": "vleiToken",
        "Wrapper (wvLEI)": "wrapper",
    }
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for label, key in labels.items():
            prefix = f"{label}:"
            if line.startswith(prefix):
                result[key] = line.split(prefix, 1)[1].strip()

    missing = sorted(set(labels.values()) - set(result))
    if missing:
        raise ValueError(f"deployment log missing addresses: {', '.join(missing)}")
    return result


def load_json_file(path: str | Path) -> dict[str, Any]:
    import json

    return json.loads(Path(path).read_text())

"""Deployment-log helpers for local vlei-comply runs.

The e2e deploys contracts with Forge and then feeds the console log through the
CLI. Keeping parsing here lets shell stay simple while avoiding a runtime
dependency on Foundry artifact internals.
"""

from __future__ import annotations

def parse_deploy_log(text: str) -> dict[str, str]:
    """Parse DeployAll console output into contract addresses."""

    labels = {
        "AdminControls": "adminControls",
        "Vendor IdentityRegistry": "identityRegistry",
        "CredentialRegistry": "credentialRegistry",
        "VLEIOracleRegistry": "oracleRegistry",
        "Asset (USDC)": "usdcToken",
        "Wrapper (wUSDC)": "wrapper",
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

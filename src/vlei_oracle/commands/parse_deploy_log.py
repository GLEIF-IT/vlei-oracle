"""`vlei-oracle parse-deploy-log` command."""

from __future__ import annotations

import json
from pathlib import Path


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


def add_parser(subparsers) -> None:
    """Register the local Foundry deploy-log parser command."""

    parser = subparsers.add_parser("parse-deploy-log", help="Parse DeployAll stdout into contracts JSON")
    parser.add_argument("--log", required=True)
    parser.add_argument("--out", required=True)
    parser.set_defaults(handler=handler)


def handler(args) -> None:
    """Write deployment addresses for inspection and explicit service flags."""

    parsed = parse_deploy_log(Path(args.log).read_text())
    Path(args.out).write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n")

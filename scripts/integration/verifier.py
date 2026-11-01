"""Invoke the Verifier fixture CLI through its explicit run artifacts."""

import json
import subprocess
from pathlib import Path

from .artifacts import load_json


def verifier_command(root: Path, *arguments: str) -> list[str]:
    """Address the prepared Verifier fixture CLI through its explicit artifact."""
    configuration = load_json(root / "verifier/run.json")
    return [
        configuration["python"],
        "-m",
        "scripts.integration.env",
        *arguments,
        "--run-dir",
        str(root / "verifier"),
    ]


def call_verifier(
    root: Path, *arguments: str, descriptor: bool = False, visible: bool = False
):
    """Execute a public fixture operation; optionally return its JSON descriptor.

    Native issuer diagnostics go to a retained log. No Verifier Python module is
    imported by Oracle tooling, and failed commands never yield a stale descriptor.
    """
    with (root / "logs/verifier-tool.log").open("a") as log:
        if descriptor:
            output = subprocess.check_output(
                verifier_command(root, *arguments),
                stderr=log,
                text=True,
                timeout=150,
                cwd=Path(load_json(root / "verifier/run.json")["tool"]).parents[2],
            )
            return json.loads(output)
        subprocess.run(
            verifier_command(root, *arguments),
            check=True,
            timeout=240,
            stdout=None if visible else log,
            stderr=log,
            cwd=Path(load_json(root / "verifier/run.json")["tool"]).parents[2],
        )


def start_verifier(root: Path, configuration: dict) -> None:
    """Start/reopen Verifier through its supported external-RPC/webhook mode."""
    call_verifier(
        root,
        "start",
        "--rpc-url",
        configuration["rpcUrl"],
        "--webhook-url",
        configuration["webhookUrl"],
        "--peer-kel",
        str(root / "oracle.cesr"),
    )

"""Consume Comply-owned local deployment through its command and JSON artifact.

Oracle coordinates deployment timing; Comply owns construction, artifact fields
and post-broadcast checks. No stdout labels or stale broadcast files are parsed.
"""

import os
from pathlib import Path
import subprocess
from uuid import uuid4

from .artifacts import load_json, save_json


def deploy(comply: Path, rpc_url: str, destination: Path, deployer_key: str) -> dict:
    """Deploy once on local Anvil and return the named addresses.

    comply is a run-owned checkout. destination receives a copy only after its
    deploy command succeeds. The key travels in the child environment, never its
    command line or the artifact. Preserve the complete Forge log on failure.
    """
    comply, destination = Path(comply).resolve(), Path(destination).resolve()
    output = Path("deployments") / str(uuid4()) / "contracts.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    log_path = destination.with_suffix(".log")
    with log_path.open("w") as log:
        subprocess.run(
            [
                "bash",
                str(comply / "scripts/deploy-local.sh"),
                "--rpc-url",
                rpc_url,
                "--output",
                str(output),
            ],
            cwd=comply,
            env={**os.environ, "PRIVATE_KEY": deployer_key},
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=True,
        )
    artifact = load_json(comply / output)
    if artifact["version"] != 1 or artifact["chainId"] != 31337:
        raise ValueError(f"Incompatible deployment artifact; inspect {log_path}")
    save_json(destination, artifact)
    return artifact["contracts"]

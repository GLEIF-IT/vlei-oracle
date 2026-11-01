"""Prepare explicit source snapshots, installed wheels and the paired run artifact."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from .artifacts import OWNER, copy_tool_inputs, load_json, save_json, snapshot_source
from .builds import install_oracle
from .checkouts import fetch_checkout
from .verifier import call_verifier


def prepare_run(
    root: Path,
    oracle_source: Path,
    *,
    verifier_source: Path | None,
    comply_source: Path | None,
    witness_count: int,
    wheel: Path | None = None,
    tool_revision: str | None = None,
) -> None:
    """Create a fresh stack from exact pins or snapshotted local candidates.

    Never copy .git, ignored operator data or live stores. Contracts are always
    run-owned so Forge output cannot dirty an operator's checkout. Preparation
    issues the source chain through Verifier's CLI but leaves no services running.
    """
    for source in (oracle_source, verifier_source, comply_source):
        if source is not None and root.resolve().is_relative_to(source.resolve()):
            raise ValueError("Run directory must be outside selected source checkouts")
    for executable in ("anvil", "forge", "cast", "jq"):
        if not shutil.which(executable):
            raise ValueError(f"Missing required local tooling: {executable}")
    if root.exists():
        raise ValueError("Choose a fresh run directory")
    root.mkdir(parents=True, mode=0o700)
    for name in ("logs", "checkouts", "tools", "wheels"):
        (root / name).mkdir()
    pins = load_json(oracle_source / "scripts/integration/dependencies.json")
    verifier_source = _select_source(
        root, "verifier", verifier_source, pins["verifier"]
    )
    comply_source = _select_source(root, "comply", comply_source, pins["comply"])
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.integration.env",
                "prepare",
                "--verifier-source",
                str(verifier_source),
                "--run-dir",
                str(root / "verifier"),
                "--witness-count",
                str(witness_count),
            ],
            check=True,
            timeout=240,
            cwd=verifier_source,
        )
    except BaseException as error:
        # A killed preparation controller cannot unwind its separate witness session.
        # Use the recorded owner to stop it, preserving the original failure/logs.
        try:
            if (root / "verifier/run.json").exists():
                call_verifier(root, "stop")
        except Exception as cleanup_error:
            error.add_note(f"Verifier cleanup also failed: {cleanup_error}")
        raise
    verifier = load_json(root / "verifier/run.json")
    snapshot = root / "tools/oracle"
    if wheel is None:
        provenance = snapshot_source(oracle_source, snapshot)
    else:
        if not tool_revision:
            raise ValueError("--oracle-wheel requires --tool-revision")
        provenance = (
            snapshot_source(oracle_source, snapshot)
            if (oracle_source / ".git").exists()
            else copy_tool_inputs(oracle_source, snapshot)
        )
        provenance["revision"] = tool_revision
    wheel = install_oracle(root, snapshot, verifier["python"], wheel)
    from .processes import free_port

    used = {
        verifier["rpcPort"],
        verifier["verifierPort"],
        verifier["receiverPort"],
        verifier["schemaPort"],
        *(w["port"] for w in verifier["witnesses"]),
    }
    oracle_port = free_port()
    while oracle_port in used:
        oracle_port = free_port()
    proxy_port = free_port()
    while proxy_port in used or proxy_port == oracle_port:
        proxy_port = free_port()
    configuration = {
        "owner": OWNER,
        "root": str(root),
        "python": verifier["python"],
        "tool": str(snapshot / "scripts/integration/env.py"),
        "comply": str(comply_source),
        "rpcUrl": verifier["rpcUrl"],
        "rpcPort": verifier["rpcPort"],
        "anvil": verifier["anvil"],
        "oraclePort": oracle_port,
        "proxyPort": proxy_port,
        "webhookUrl": f"http://127.0.0.1:{proxy_port}/v1/status-events",
        "oracleUrl": f"http://127.0.0.1:{oracle_port}",
        "wheelSha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "source": provenance,
    }
    save_json(root / "run.json", configuration)
    subprocess.run(
        [
            configuration["python"],
            "-m",
            "scripts.integration.env",
            "_initialize",
            "--run-dir",
            str(root),
        ],
        check=True,
        cwd=snapshot,
    )


def _select_source(root: Path, name: str, candidate: Path | None, pin: dict) -> Path:
    """Fetch a clean exact revision, or copy only Git-inventoried candidate files."""
    destination = root / "checkouts" / name
    if candidate is None:
        fetch_checkout(destination, pin["url"], pin["commit"])
    else:
        provenance = snapshot_source(candidate.resolve(), destination)
        save_json(root / f"{name}-source.json", provenance)
        # Verifier prepare needs its candidate's Git provenance. It snapshots that
        # source itself; the copy above records/isolate inputs but is not an import.
        if name == "verifier":
            return candidate.resolve()
    return destination

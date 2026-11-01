"""Sequence start, stop, restart and qualification for the owned paired environment."""

import os
import shutil
import subprocess
from pathlib import Path

from .artifacts import load_run
from .chain import ensure_deployment, start_chain
from .processes import is_running, process_records, stop
from .services import start_oracle, start_proxy
from .verifier import call_verifier, start_verifier


def start_run(root: Path, configuration: dict) -> None:
    """Start the complete stack; unwind only owned children if any phase fails."""
    if not configuration.get("prepared"):
        raise ValueError("Preparation did not complete; inspect retained logs")
    try:
        start_chain(root, configuration)
        ensure_deployment(root, configuration)
        start_oracle(root, configuration)
        start_proxy(root, configuration)
        start_verifier(root, configuration)
    except BaseException:
        stop_run(root)
        raise


def stop_run(root: Path) -> None:
    """Stop the Verifier-owned sub-environment, then Oracle and its chain."""
    try:
        if (root / "verifier/run.json").exists():
            call_verifier(root, "stop")
    finally:
        stop(root)


def clean_run(root: Path) -> None:
    """Remove only an explicitly owned stack with no live child in either owner."""
    load_run(root)
    records = [
        *process_records(root).values(),
        *process_records(root / "verifier").values(),
    ]
    if any(is_running(record) for record in records):
        raise ValueError("Stop the run before cleaning")
    shutil.rmtree(root)


def run_flow(root: Path, configuration: dict) -> None:
    """Run the sole maintained USDC scenario against this explicit started stack."""
    environment = {
        **os.environ,
        "ORACLE_INTEGRATION_RUN": str(root),
        "VLEI_COMPLY_DIR": configuration["comply"],
    }
    subprocess.run(
        [
            configuration["python"],
            "-m",
            "pytest",
            "-q",
            "tests/qualification/test_two_services.py",
        ],
        cwd=Path(configuration["tool"]).parents[2],
        env=environment,
        check=True,
        timeout=240,
    )


def restart_oracle(root: Path, configuration: dict) -> None:
    """Restart only Oracle while retaining its independent selection/journal stores."""
    stop(root, "oracle")
    start_oracle(root, configuration)

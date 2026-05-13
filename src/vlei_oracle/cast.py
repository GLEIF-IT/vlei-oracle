"""Small wrappers around Foundry cast.

The oracle intentionally delegates Ethereum signature and keccak behavior to
Foundry in local demos so the verifier and shell workflow agree exactly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def find_cast(explicit: str | None = None) -> str:
    """Return a usable cast binary path or raise FileNotFoundError."""

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_cast = os.environ.get("CAST_BIN")
    if env_cast:
        candidates.append(env_cast)
    found = shutil.which("cast")
    if found:
        candidates.append(found)
    home = Path.home()
    candidates.append(str(home / ".foundry" / "bin" / "cast"))

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists() and os.access(path, os.X_OK):
            return str(path)

    raise FileNotFoundError("cast not found; install Foundry or set CAST_BIN")


def run_cast(args: list[str], *, cast_bin: str | None = None) -> str:
    """Run cast and return stripped stdout."""

    binary = find_cast(cast_bin)
    completed = subprocess.run(
        [binary, *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def keccak(value: str, *, cast_bin: str | None = None) -> str:
    """Return cast's keccak hash for a string value."""

    return run_cast(["keccak", value], cast_bin=cast_bin)


def wallet_address(private_key: str, *, cast_bin: str | None = None) -> str:
    """Return the wallet address for a private key."""

    return run_cast(["wallet", "address", "--private-key", private_key], cast_bin=cast_bin)


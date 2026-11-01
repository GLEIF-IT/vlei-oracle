"""Build or copy the selected Oracle wheel and install its tooling dependencies."""

import shutil
import subprocess
from pathlib import Path


def install_oracle(root: Path, source: Path, python: str, wheel: Path | None) -> Path:
    """Build/install an Oracle wheel into the combined tooling venv, retaining logs."""
    with (root / "logs/build.log").open("w") as log:
        if wheel is None:
            subprocess.run(
                [
                    python,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--wheel-dir",
                    str(root / "wheels"),
                    str(source),
                ],
                check=True,
                stdout=log,
                stderr=log,
            )
            wheel = next((root / "wheels").glob("vlei_oracle-*.whl"))
        else:
            copied = root / "wheels" / wheel.name
            shutil.copy2(wheel, copied)
            wheel = copied
        subprocess.run(
            [python, "-m", "pip", "install", str(wheel) + "[dev]"],
            check=True,
            stdout=log,
            stderr=log,
        )
    return wheel

"""Initialize the Oracle signing identity and record exact runtime versions."""

import subprocess
import sys
from pathlib import Path

from .artifacts import save_json


def initialize_identity(root: Path, configuration: dict) -> None:
    """Prepare Oracle's own AID for public OOBI serving before either service opens stores."""
    from importlib.metadata import version

    from keri.app import habbing

    configuration["versions"] = {
        "python": sys.version.split()[0],
        **{
            name: version(name)
            for name in ("vlei-oracle", "eoa-verifier", "gleif_keri", "gleif_hio")
        },
        **{
            name: subprocess.check_output(
                [name, "--version"], text=True, timeout=5
            ).strip()
            for name in ("anvil", "forge", "cast", "jq")
        },
    }
    with habbing.openHby(
        name="oracle", headDirPath=str(root / "services"), temp=False
    ) as hby:
        hab = hby.habByName("oracle") or hby.makeHab(name="oracle")
        configuration["oracleAid"] = hab.pre
    configuration["prepared"] = True
    save_json(root / "run.json", configuration)

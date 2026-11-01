"""Launch the independent Oracle service and lost-response proxy."""

import os
from pathlib import Path
from urllib.request import urlopen

from .artifacts import load_json
from .chain import POSTING_KEY
from .processes import launch, wait_ready


def start_oracle(root: Path, configuration: dict) -> None:
    """Start the installed Oracle with explicit peer, chain, writer and contracts."""
    verifier = load_json(root / "verifier/run.json")
    addresses = configuration["contracts"]
    command = [
        str(Path(configuration["python"]).with_name("vlei-oracle")),
        "server",
        "start",
        "--name",
        "oracle",
        "--alias",
        "oracle",
        "--head-dir",
        str(root / "services"),
        "--host",
        "127.0.0.1",
        "--http-port",
        str(configuration["oraclePort"]),
        "--verifier-aid",
        verifier["verifierAid"],
        "--verifier-kel",
        str(root / "verifier/verifier.cesr"),
        "--chain-id",
        "31337",
        "--webhook-url",
        configuration["webhookUrl"],
        "--rpc-url",
        configuration["rpcUrl"],
        "--endorsement-registry",
        addresses["oracleRegistry"],
        "--identity-registry",
        addresses["identityRegistry"],
        "--confirmations",
        "1",
        "--posting-interval",
        ".1",
    ]
    launch(
        root,
        "oracle",
        command,
        port=configuration["oraclePort"],
        env={**os.environ, "VLEI_ORACLE_POSTING_KEY": POSTING_KEY},
    )
    url = configuration["oracleUrl"] + "/oobi/" + configuration["oracleAid"]
    wait_ready(root, "oracle", lambda: bool(urlopen(url, timeout=2).read()))


def start_proxy(root: Path, configuration: dict) -> None:
    """Start the explicit fault-injection fixture before Verifier sends events."""
    command = [
        configuration["python"],
        "-m",
        "scripts.integration.proxy",
        "--root",
        str(root),
    ]
    launch(
        root,
        "proxy",
        command,
        port=configuration["proxyPort"],
        cwd=Path(configuration["tool"]).parents[2],
    )
    wait_ready(
        root,
        "proxy",
        lambda: bool(
            urlopen(
                f"http://127.0.0.1:{configuration['proxyPort']}/events", timeout=2
            ).read()
        ),
    )

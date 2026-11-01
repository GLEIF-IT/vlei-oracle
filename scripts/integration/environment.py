"""Own the local USDC stack without absorbing Verifier or Solidity workflows.

This module selects explicit artifacts and sequences resource lifetimes. The
Verifier fixture CLI owns witnessed issuance; Comply's deploy command owns all
contract construction. The maintained Oracle pytest scenario owns USDC assertions.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen

from .artifacts import load_json, save_json, snapshot_source, copy_tool_inputs
from .checkouts import fetch_checkout
from .contracts import deploy
from .processes import is_running, launch, process_records, stop, wait_ready

OWNER = "vlei-oracle-integration"
# Public Anvil fixture keys only. The posting writer is distinct from deployment.
DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
POSTING_KEY = "0x" + "0" * 63 + "3"


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
    wheel = _install_oracle(root, snapshot, verifier["python"], wheel)
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


def _install_oracle(root: Path, source: Path, python: str, wheel: Path | None) -> Path:
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


def initialize_identity(root: Path, configuration: dict) -> None:
    """Prepare Oracle's own AID and public KEL before either service opens stores."""
    from keri.app import habbing
    from importlib.metadata import version

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
        (root / "oracle.cesr").write_bytes(hab.makeOwnEvent(sn=0))
    configuration["prepared"] = True
    save_json(root / "run.json", configuration)


def load_run(root: Path) -> dict:
    """Require the exact owned run before inspecting, starting or cleaning it."""
    configuration = load_json(root / "run.json")
    if configuration["owner"] != OWNER or configuration["root"] != str(root):
        raise ValueError("Run ownership mismatch")
    return configuration


def rpc(configuration: dict, method: str, parameters: list):
    """Perform one bounded JSON-RPC call on this run's explicit local chain."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": parameters}
    ).encode()
    request = Request(
        configuration["rpcUrl"], data=body, headers={"Content-Type": "application/json"}
    )
    with urlopen(request, timeout=5) as response:
        value = json.loads(response.read())
    if "error" in value:
        raise ValueError(value["error"])
    return value["result"]


def start_run(root: Path, configuration: dict) -> None:
    """Start the complete stack; unwind only owned children if any phase fails."""
    if not configuration.get("prepared"):
        raise ValueError("Preparation did not complete; inspect retained logs")
    try:
        start_chain(root, configuration)
        _ensure_deployment(root, configuration)
        _start_oracle(root, configuration)
        _start_proxy(root, configuration)
        start_verifier(root, configuration)
    except BaseException:
        stop_run(root)
        raise


def start_chain(root: Path, configuration: dict) -> None:
    """Start/reopen Oracle's local Anvil with persisted state and verify chain ID."""
    launch(
        root,
        "anvil",
        [
            configuration["anvil"],
            "--host",
            "127.0.0.1",
            "--port",
            str(configuration["rpcPort"]),
            "--chain-id",
            "31337",
            "--state",
            str(root / "anvil.json"),
            "--state-interval",
            "1",
            "--silent",
        ],
        port=configuration["rpcPort"],
    )
    wait_ready(root, "anvil", lambda: rpc(configuration, "eth_chainId", []) == "0x7a69")


def _ensure_deployment(root: Path, configuration: dict) -> None:
    """Deploy once, or require the existing chain still contains the recorded code."""
    artifact = root / "contracts.json"
    if artifact.exists():
        configuration["contracts"] = load_json(artifact)["contracts"]
        if any(
            rpc(configuration, "eth_getCode", [address, "latest"]) == "0x"
            for address in configuration["contracts"].values()
        ):
            raise ValueError("Chain no longer matches deployment; choose a fresh run")
    else:
        configuration["contracts"] = deploy(
            Path(configuration["comply"]),
            configuration["rpcUrl"],
            artifact,
            DEPLOYER_KEY,
        )
    _authorize_writer(configuration)
    save_json(root / "run.json", configuration)


def _authorize_writer(configuration: dict) -> None:
    """Fund and authorize the distinct local posting writer through public RPC."""
    from eth_account import Account
    from web3 import Web3

    writer = Account.from_key(POSTING_KEY).address
    admin = configuration["contracts"]["adminControls"]
    role = Web3.to_hex(Web3.keccak(text="COMPLIANCE_ADMIN_ROLE"))
    present = (
        subprocess.check_output(
            [
                "cast",
                "call",
                "--rpc-url",
                configuration["rpcUrl"],
                admin,
                "hasRole(bytes32,address)(bool)",
                role,
                writer,
            ],
            text=True,
            timeout=10,
        ).strip()
        == "true"
    )
    if not present and configuration.get("writerAuthorized"):
        raise ValueError(
            "Writer authorization changed; do not silently repair an established run"
        )
    if not present:
        rpc(configuration, "anvil_setBalance", [writer, "0x56bc75e2d63100000"])
        subprocess.run(
            [
                "cast",
                "send",
                "--unlocked",
                "--from",
                Account.from_key(DEPLOYER_KEY).address,
                "--rpc-url",
                configuration["rpcUrl"],
                admin,
                "grantRole(bytes32,address)",
                role,
                writer,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    configuration["writerAuthorized"] = True


def _start_oracle(root: Path, configuration: dict) -> None:
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


def start_verifier(root: Path, configuration: dict) -> None:
    """Start/reopen Verifier through its supported external-RPC/webhook mode."""
    call_verifier(
        root,
        "start",
        "--rpc-url",
        configuration["rpcUrl"],
        "--webhook-url",
        configuration["webhookUrl"],
        "--webhook-aid",
        configuration["oracleAid"],
        "--peer-kel",
        str(root / "oracle.cesr"),
    )


def restart_oracle(root: Path, configuration: dict) -> None:
    """Restart only Oracle while retaining its independent selection/journal stores."""
    stop(root, "oracle")
    _start_oracle(root, configuration)


def _start_proxy(root: Path, configuration: dict) -> None:
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

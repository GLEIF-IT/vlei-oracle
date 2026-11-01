"""Own the local chain, contract deployment and fixture writer authorization."""

import json
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

from .artifacts import load_json, save_json
from .contracts import deploy
from .processes import launch, wait_ready

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
POSTING_KEY = "0x" + "0" * 63 + "3"


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


def ensure_deployment(root: Path, configuration: dict) -> None:
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

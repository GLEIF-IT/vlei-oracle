"""Local test deployment and token transactions; no production issuer logic."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from eth_account import Account
from web3 import Web3

from vlei_oracle.contracts import parse_deploy_log

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
POSTER_KEY = "0x" + "0" * 63 + "3"


def deploy(comply, rpc_url, artifacts):
    """Run Forge in its own process while witness/oracle HIO tasks continue."""
    with (artifacts / "deploy.log").open("w+") as log:
        with subprocess.Popen(
            [shutil.which("forge") or "forge", "script", "script/DeployAll.s.sol",
             "--rpc-url", rpc_url, "--broadcast", "--non-interactive"],
            cwd=comply, env={**os.environ, "PRIVATE_KEY": DEPLOYER_KEY},
            stdout=log, stderr=subprocess.STDOUT,
        ) as process:
            try:
                while process.poll() is None:
                    yield 0.01
                log.seek(0)
                output = log.read()
                assert process.returncode == 0, output
                return parse_deploy_log(output)
            finally:
                if process.poll() is None:
                    process.terminate()


def contract(comply, name, address):
    """Read the actual compiled fixture ABI for local encoding only."""
    artifact = json.loads((Path(comply) / "out" / (name + ".sol") / (name + ".json")).read_text())
    return Web3().eth.contract(address=Web3.to_checksum_address(address), abi=artifact["abi"])


def read(effects, target, function, args=(), *, sender=None):
    """Perform an actual local contract query through HIO."""
    call = {"to": target.address, "data": target.encode_abi(function, args=list(args))}
    if sender:
        call["from"] = sender
    raw = yield from effects.request("eth_call", [call, "latest"])
    outputs = target.get_function_by_name(function).abi["outputs"]
    types = [output["type"] for output in outputs]
    return effects.web3.codec.decode(types, bytes.fromhex(raw[2:]))


def send(effects, target, function, args, key):
    """Sign and execute one test-controller transaction on the isolated chain."""
    account = Account.from_key(key)
    tx = {"from": account.address, "to": target.address, "value": 0,
          "data": target.encode_abi(function, args=list(args))}
    nonce = yield from effects.request("eth_getTransactionCount", [account.address, "pending"])
    price = yield from effects.request("eth_gasPrice", [])
    gas = yield from effects.request("eth_estimateGas", [tx])
    tx.update(nonce=int(nonce, 16), gasPrice=int(price, 16),
              gas=int(gas, 16) + 10000, chainId=31337)
    signed = account.sign_transaction(tx)
    tx_hash = Web3.to_hex(signed.hash)
    yield from effects.submit(Web3.to_hex(signed.raw_transaction), tx_hash)
    while (receipt := (yield from effects.receipt(tx_hash))) is None:
        yield 0.01
    assert receipt["status"] == "0x1", receipt
    return receipt

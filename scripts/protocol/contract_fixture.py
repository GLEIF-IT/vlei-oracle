"""Local test deployment and token transactions; no production issuer logic."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from time import monotonic

from eth_account import Account
from web3 import Web3

from vlei_oracle.commands.parse_deploy_log import parse_deploy_log
from vlei_oracle.endorsement.effects import ContractEffects
from vlei_oracle.endorsement.reconciling import EndorsementDoer
from vlei_oracle.verification.credentials import reconstruct_binding

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
              gas=int(gas, 16) + 10000, chainId=effects.chain_id)
    signed = account.sign_transaction(tx)
    tx_hash = Web3.to_hex(signed.hash)
    yield from effects.submit(Web3.to_hex(signed.raw_transaction), tx_hash)
    while (receipt := (yield from effects.receipt(tx_hash))) is None:
        yield 0.01
    assert receipt["status"] == "0x1", receipt
    return receipt


def delegate_account(effects, *, authority_key, target):
    """Execute a real EIP-7702 authorization transaction on the local chain."""
    authority, payer = Account.from_key(authority_key), Account.from_key(DEPLOYER_KEY)
    authority_nonce = yield from effects.request("eth_getTransactionCount", [authority.address, "latest"])
    payer_nonce = yield from effects.request("eth_getTransactionCount", [payer.address, "pending"])
    price = int((yield from effects.request("eth_gasPrice", [])), 16)
    # Different payer: authorization uses the authority's current nonce, not +1.
    authorization = authority.sign_authorization({
        "chainId": effects.chain_id, "address": target.address,
        "nonce": int(authority_nonce, 16),
    })
    signed = payer.sign_transaction({
        "type": 4, "chainId": effects.chain_id, "nonce": int(payer_nonce, 16),
        "to": payer.address, "value": 0, "gas": 100000,
        "maxFeePerGas": max(2000000000, price * 2), "maxPriorityFeePerGas": 1000000000,
        "authorizationList": [authorization],
    })
    tx_hash = Web3.to_hex(signed.hash)
    yield from effects.submit(Web3.to_hex(signed.raw_transaction), tx_hash)
    while (receipt := (yield from effects.receipt(tx_hash))) is None:
        yield 0.01
    assert receipt["status"] == "0x1", receipt
    transaction = yield from effects.request("eth_getTransactionByHash", [tx_hash])
    assert transaction["type"] == "0x4"
    code = yield from effects.request("eth_getCode", [authority.address, "latest"])
    assert code == "0xef0100" + target.address[2:].lower(), code
    print(f"Actual signed EIP-7702 authorization installed delegation: {tx_hash}", flush=True)
    return receipt


def qualify_transfers(*, comply, rpc_url, artifacts, runtime, trusted_geda,
                      schema_said, sender_key, recipient, target_saids, workflow):
    """Deploy and test rejected/allowed transfers beside the native oracle."""
    account = Account.from_key(sender_key)
    addresses = yield from deploy(comply, rpc_url, artifacts)
    poster_address = Account.from_key(POSTER_KEY).address
    effects = ContractEffects(
        rpc_url=rpc_url, chain_id=31337, writer=poster_address,
        endorsement=addresses["oracleRegistry"], identity=addresses["identityRegistry"],
        confirmations=1,
    )
    # Separate test-controller effects from the dedicated oracle writer.
    fixture_effects = ContractEffects(
        rpc_url=rpc_url, chain_id=31337, writer=Account.from_key(DEPLOYER_KEY).address,
        endorsement=addresses["oracleRegistry"], identity=addresses["identityRegistry"],
        confirmations=1,
    )
    workflow.extend([fixture_effects])
    children = [fixture_effects]
    for wallet in (account.address, poster_address):
        yield from fixture_effects.request("anvil_setBalance", [wallet, "0x56bc75e2d63100000"])
    admin = contract(comply, "AdminControls", addresses["adminControls"])
    token = contract(comply, "USDC", addresses["usdcToken"])
    wrapper = contract(comply, "wUSDC", addresses["wrapper"])
    role = fixture_effects.web3.keccak(text="COMPLIANCE_ADMIN_ROLE")
    yield from send(fixture_effects, admin, "grantRole", [role, poster_address], DEPLOYER_KEY)
    yield from send(fixture_effects, token, "mint", [account.address, 100000000], DEPLOYER_KEY)
    yield from delegate_account(fixture_effects, authority_key=sender_key, target=token)
    yield from send(fixture_effects, token, "approve", [wrapper.address, 100000000], sender_key)
    assert (yield from read(fixture_effects, wrapper, "transfer", [recipient, 1000000], sender=account.address)) == (False,)
    yield from send(fixture_effects, wrapper, "transfer", [recipient, 1000000], sender_key)
    assert (yield from read(fixture_effects, token, "balanceOf", [recipient])) == (0,)

    posting = EndorsementDoer(
        baser=runtime.baser, rgy=runtime.rgy, trusted_geda_aid=trusted_geda,
        association_schema=schema_said, effects=effects, private_key=POSTER_KEY,
        interval=0.05,
    )
    started = monotonic()
    workflow.extend([posting])
    children.append(posting)
    while any(runtime.baser.get_managed(said).state != "endorsed" for said in target_saids):
        assert monotonic() - started < 60, "initial posting exceeded local 60s watchdog"
        yield 0.01
    print(f"Local initial posting completed in {monotonic() - started:.3f}s (watchdog 60s)", flush=True)
    assert (yield from fixture_effects.request("eth_getCode", [account.address, "latest"])) == (
        "0xef0100" + token.address[2:].lower()
    )
    _, endorsement = yield from fixture_effects.observe(account.address)
    assert endorsement[1] == target_saids[0]
    yield from send(fixture_effects, wrapper, "transfer", [recipient, 1000000], sender_key)
    assert (yield from read(fixture_effects, token, "balanceOf", [recipient])) == (1000000,)
    print("Real endorsements permit transfer without global wallet admission", flush=True)
    # A stale identity hint, even for the same entity and an admin
    # wallet, must never substitute for that wallet's endorsement.
    entity = reconstruct_binding(rgy=runtime.rgy, said=target_saids[0], schema_said=schema_said)["identityRef"]
    yield from send(fixture_effects, fixture_effects.contracts["identity"],
                    "registerIdentity", [entity, poster_address, b""], DEPLOYER_KEY)
    yield from send(fixture_effects, wrapper, "transfer", [poster_address, 1000000], sender_key)
    assert (yield from read(fixture_effects, token, "balanceOf", [poster_address])) == (0,)

    return fixture_effects, token, wrapper, children

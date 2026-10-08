"""Actual local contract effects; needs Foundry and the paired comply checkout."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

from eth_account import Account
from hio.base import doing
import pytest
from web3 import Web3

from vlei_oracle.basing import OracleBaser, PostingScope, TransactionRecord
from vlei_oracle.endorsement.effects import ContractEffects
from vlei_oracle.commands.parse_deploy_log import parse_deploy_log

KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
WALLET = Account.from_key("0x" + "0" * 63 + "1").address
IDENTITY = "0x" + "12" * 32


@pytest.fixture(scope="module")
def deployment():
    comply = Path(os.getenv("VLEI_COMPLY_DIR", Path(__file__).resolve().parents[2] / "vlei-comply"))
    forge, anvil = shutil.which("forge"), shutil.which("anvil")
    if not forge or not anvil or not (comply / "script/DeployAll.s.sol").is_file():
        pytest.skip("local contract tests require Foundry and VLEI_COMPLY_DIR")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    rpc = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [anvil, "--host", "127.0.0.1", "--port", str(port), "--silent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        readiness = ContractEffects(
            rpc_url=rpc, chain_id=31337, writer=Account.from_key(KEY).address,
            endorsement=WALLET, identity=WALLET,
        )
        drive(readiness, lambda effects: effects.check_chain())
        deployed = subprocess.run(
            [forge, "script", "script/DeployAll.s.sol", "--rpc-url", rpc,
             "--broadcast", "--non-interactive"],
            cwd=comply, env={**os.environ, "PRIVATE_KEY": KEY},
            capture_output=True, text=True, timeout=90,
        )
        assert deployed.returncode == 0, deployed.stdout + deployed.stderr
        yield rpc, parse_deploy_log(deployed.stdout)
    finally:
        process.terminate()
        process.wait(timeout=10)


def effects_for(deployment, *, chain_id=31337):
    rpc, addresses = deployment
    return ContractEffects(
        rpc_url=rpc, chain_id=chain_id, writer=Account.from_key(KEY).address,
        endorsement=addresses["oracleRegistry"], identity=addresses["identityRegistry"],
        confirmations=1,
    )


def drive(effects, workflow):
    """Run effect generators beside their native clients on one HIO clock."""
    results = []

    def run(tymth, tock=0.0, **opts):
        yield tock
        try:
            results.append((yield from workflow(effects)))
        finally:
            scheduler.remove([effects])

    scheduler = doing.DoDoer(doers=[effects, doing.doify(run)])
    doing.Doist(real=True, tock=0.01, limit=20).do(doers=[scheduler])
    assert results, "contract effect workflow exceeded its limit"
    return results[0]


def signed_intent(tx, *, said="association", action="publishEndorsement"):
    signed = Account.from_key(KEY).sign_transaction(tx)
    return TransactionRecord(
        txHash=Web3.to_hex(signed.hash), rawTransaction=Web3.to_hex(signed.raw_transaction),
        transaction=tx, associationSaid=said, action=action,
    )


def test_python_encoding_matches_owned_solidity_vector(deployment):
    comply = Path(os.getenv("VLEI_COMPLY_DIR", Path(__file__).resolve().parents[2] / "vlei-comply"))
    vector = json.loads(
        (comply / "abi/endorsement-vector.json").read_text()
    )
    contracts = effects_for(deployment).contracts
    contract = contracts["endorsement"]
    assert contract.abi == json.loads((comply / "abi/VLEIOracleRegistry.json").read_text())
    assert contracts["identity"].abi == json.loads((comply / "out/IdentityRegistry.sol/IdentityRegistry.json").read_text())["abi"]
    assert contract.encode_abi("publishEndorsement", args=[
        Web3.to_checksum_address(vector["wallet"]), vector["identityRef"], vector["associationSaid"],
    ]) == vector["calldata"]


def test_lost_submission_result_recovers_saved_identity(deployment, tmp_path):
    store = OracleBaser(name="posting", headDirPath=str(tmp_path))

    def submit(effects):
        tx = yield from effects.prepare(("endorsement", "publishEndorsement", [WALLET, IDENTITY, "association"]))
        intent = store.pin_transaction(signed_intent(tx))
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        # Deliberately lose all in-memory effect/receipt progress.
        return intent

    intent = drive(effects_for(deployment), submit)
    store.close()
    reopened = OracleBaser(name="posting", headDirPath=str(tmp_path))
    try:
        saved = reopened.unfinished_transaction()
        assert saved == intent

        def recover(effects):
            result = yield from effects.reconcile(saved)
            identity, endorsement = yield from effects.observe(WALLET)
            nonce = yield from effects.request("eth_getTransactionCount", [effects.writer, "latest"])
            assert endorsement == (IDENTITY, "association")
            assert int(nonce, 16) == saved.transaction["nonce"] + 1
            return result

        kin, tx_hash, receipt = drive(effects_for(deployment), recover)
        assert kin == "receipt" and tx_hash == saved.txHash
        assert receipt["confirmed"] and receipt["status"] == "0x1"
    finally:
        reopened.close(clear=True)


def test_explicit_same_nonce_cancellation_prevents_stale_publication(deployment, tmp_path):
    store = OracleBaser(name="cancel", headDirPath=str(tmp_path))

    def workflow(effects):
        yield from effects.request("anvil_setAutomine", [False])
        try:
            tx = yield from effects.prepare(("endorsement", "publishEndorsement", [WALLET, IDENTITY, "stale"]))
            intent = store.pin_transaction(signed_intent(tx, said="stale"))
            yield from effects.submit(intent.rawTransaction, intent.txHash)
            assert (yield from effects.reconcile(intent))[0] == "pending"
            cancel = yield from effects.cancellation(intent)
            signed = Account.from_key(KEY).sign_transaction(cancel)
            intent.cancelHash, intent.cancelRaw = Web3.to_hex(signed.hash), Web3.to_hex(signed.raw_transaction)
            store.pin_transaction(intent)
            yield from effects.submit(intent.cancelRaw, intent.cancelHash)
            yield from effects.request("evm_mine", [])
            result = yield from effects.reconcile(intent)
            _, endorsement = yield from effects.observe(WALLET)
            assert endorsement[1] != "stale"
            assert result[1] == intent.cancelHash and result[2]["status"] == "0x1"
            return result
        finally:
            yield from effects.request("anvil_setAutomine", [True])

    try:
        drive(effects_for(deployment), workflow)
    finally:
        store.close(clear=True)


def test_reorg_reconciles_and_rebroadcasts_the_same_identity(deployment):
    def workflow(effects):
        snapshot = yield from effects.request("evm_snapshot", [])
        tx = yield from effects.prepare(("endorsement", "publishEndorsement", [WALLET, IDENTITY, "reorg"]))
        intent = signed_intent(tx, said="reorg")
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        assert (yield from effects.reconcile(intent))[0] == "receipt"
        assert (yield from effects.request("evm_revert", [snapshot])) is True
        assert (yield from effects.reconcile(intent))[0] == "absent"
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        result = yield from effects.reconcile(intent)
        assert result[0] == "receipt" and result[1] == intent.txHash
        return result

    drive(effects_for(deployment), workflow)


def test_wrong_chain_cannot_prepare_a_transaction(deployment):
    def workflow(effects):
        with pytest.raises(ValueError, match="posting deployment"):
            yield from effects.prepare(("endorsement", "publishEndorsement", [WALLET, IDENTITY, "wrong-chain"]))
        return True

    drive(effects_for(deployment, chain_id=31338), workflow)


def test_receipt_waits_for_configured_confirmations(deployment):
    effects = effects_for(deployment)
    effects.confirmations = 2

    def workflow(effects):
        tx = yield from effects.prepare(("endorsement", "publishEndorsement", [WALLET, IDENTITY, "confirmations"]))
        intent = signed_intent(tx, said="confirmations")
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        receipt = (yield from effects.reconcile(intent))[2]
        assert receipt["confirmed"] is False
        yield from effects.request("evm_mine", [])
        assert (yield from effects.reconcile(intent))[2]["confirmed"] is True
        return True

    drive(effects, workflow)


def test_unknown_nonce_consumption_requires_reconciliation(deployment):
    def workflow(effects):
        tx = yield from effects.prepare(("endorsement", "publishEndorsement", [WALLET, IDENTITY, "never-sent"]))
        intent = signed_intent(tx, said="never-sent")
        # Consume the reserved nonce with an unrelated transaction, as another
        # process sharing the writer key would. The original hash is absent.
        foreign = signed_intent((yield from effects.cancellation(intent)))
        yield from effects.submit(foreign.rawTransaction, foreign.txHash)
        assert (yield from effects.reconcile(intent))[0] == "conflict"
        return True

    drive(effects_for(deployment), workflow)


def test_operation_store_refuses_different_deployment_scope(tmp_path):
    store = OracleBaser(name="scope", headDirPath=str(tmp_path))
    scope = PostingScope(31337, WALLET.lower(), WALLET.lower(), WALLET.lower())
    store.bind_posting_scope(scope)
    store.close()
    reopened = OracleBaser(name="scope", headDirPath=str(tmp_path))
    try:
        reopened.bind_posting_scope(scope)
        with pytest.raises(ValueError, match="posting scope"):
            reopened.bind_posting_scope(PostingScope(31338, scope.writer, scope.endorsement, scope.identity))
    finally:
        reopened.close(clear=True)

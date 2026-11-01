"""endorsement/test_effects.py."""

import json
import socket

import falcon
import pytest
from eth_account import Account
from hio.base import doing
from hio.core import http
from web3 import Web3

from tests.support.contracts import (
    drive,
    effects_for,
    observe,
    pending_intent,
    persist_intent,
    signed_intent,
)
from vlei_oracle.associations.statusing import read_status
from vlei_oracle.endorsement.effects import ContractEffects
from vlei_oracle.persistence.basing import OracleBaser

WALLET = Account.from_key("0x" + "0" * 63 + "1").address

IDENTITY = "0x" + "12" * 32


def test_python_encoding_matches_owned_solidity_vector(deployment, comply_checkout):
    """Compare exact calldata and freshly compiled ABIs from the deployed inputs."""
    comply = comply_checkout
    vector = json.loads((comply / "abi/endorsement-vector.json").read_text())
    # The contract fixture owns mapping/calldata; proof conformance lives in Verifier.
    facts = {
        "issuerAid": "DIqI4910CfGV_VLbLTy6XXLKZwm_HZQSG_N0iAG0D29c",
        "leSaid": "EDtvFDCx-Kjtac85bs5PenfQlm41Y5BlWlpooRhF-RGn",
        "extId": "eip155:1:" + vector["wallet"],
        "issuanceAnchor": {
            "sequenceNumber": 3,
            "said": "EIMtYfwyE2KoT65Tze54g0IyPfw41Zly2sXvo9Mrgzv3",
        },
    }
    _, mapped = read_status(
        json.dumps(
            {
                "version": "1",
                "eventId": vector["associationSaid"] + ":1",
                "associationSaid": vector["associationSaid"],
                "statusRevision": 1,
                "status": "accepted",
                "association": facts,
            }
        ).encode(),
        chain_id=1,
    )
    assert mapped.associationSaid == vector["associationSaid"]
    assert mapped.identityRef == vector["identityRef"]
    assert mapped.wallet == vector["wallet"]
    assert "expiresAt" not in json.dumps(vector)
    contracts = effects_for(deployment).contracts
    contract = contracts["endorsement"]
    assert contract.abi == json.loads(
        (comply / "abi/VLEIOracleRegistry.json").read_text()
    )
    assert (
        contracts["identity"].abi
        == json.loads(
            (comply / "out/IdentityRegistry.sol/IdentityRegistry.json").read_text()
        )["abi"]
    )
    assert (
        contract.encode_abi(
            "publishEndorsement",
            args=[
                Web3.to_checksum_address(vector["wallet"]),
                vector["identityRef"],
                vector["associationSaid"],
            ],
        )
        == vector["calldata"]
    )


def test_lost_submission_result_recovers_saved_identity(deployment, tmp_path):
    store = OracleBaser(name="posting", headDirPath=str(tmp_path))

    def submit(effects):
        tx = yield from effects.prepare(
            ("endorsement", "publishEndorsement", [WALLET, IDENTITY, "association"])
        )
        intent = persist_intent(store, signed_intent(tx))
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        # Deliberately lose all in-memory effect/receipt progress.
        return intent

    intent = drive(effects_for(deployment), submit)
    store.close()
    reopened = OracleBaser(name="posting", headDirPath=str(tmp_path))
    try:
        saved = pending_intent(reopened)
        assert saved == intent

        def recover(effects):
            result = yield from effects.receipt(saved.txHash)
            identity, endorsement = yield from observe(effects, WALLET)
            nonce = yield from effects.request(
                "eth_getTransactionCount", [effects.writer, "latest"]
            )
            assert endorsement == (IDENTITY, "association")
            assert int(nonce, 16) == saved.nonce + 1
            return result

        receipt = drive(effects_for(deployment), recover)
        assert receipt["confirmed"] and receipt["status"] == "0x1"
    finally:
        reopened.close(clear=True)


def test_receipt_disappears_after_reorg_and_same_bytes_can_be_submitted(deployment):
    def workflow(effects):
        snapshot = yield from effects.request("evm_snapshot", [])
        tx = yield from effects.prepare(
            ("endorsement", "publishEndorsement", [WALLET, IDENTITY, "reorg"])
        )
        intent = signed_intent(tx, said="reorg")
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        assert (yield from effects.receipt(intent.txHash))["status"] == "0x1"
        assert (yield from effects.request("evm_revert", [snapshot])) is True
        assert (yield from effects.receipt(intent.txHash)) is None
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        result = yield from effects.receipt(intent.txHash)
        assert result["status"] == "0x1"
        return result

    drive(effects_for(deployment), workflow)


def test_wrong_chain_cannot_prepare_a_transaction(deployment):
    def workflow(effects):
        with pytest.raises(ValueError, match="posting deployment"):
            yield from effects.prepare(
                ("endorsement", "publishEndorsement", [WALLET, IDENTITY, "wrong-chain"])
            )
        return True

    drive(effects_for(deployment, chain_id=31338), workflow)


def test_receipt_waits_for_configured_confirmations(deployment):
    effects = effects_for(deployment)
    effects.confirmations = 2

    def workflow(effects):
        tx = yield from effects.prepare(
            ("endorsement", "publishEndorsement", [WALLET, IDENTITY, "confirmations"])
        )
        intent = signed_intent(tx, said="confirmations")
        yield from effects.submit(intent.rawTransaction, intent.txHash)
        receipt = yield from effects.receipt(intent.txHash)
        assert receipt["confirmed"] is False
        yield from effects.request("evm_mine", [])
        assert (yield from effects.receipt(intent.txHash))["confirmed"] is True
        return True

    drive(effects, workflow)


@pytest.mark.parametrize(
    "field,value", [("transactionHash", "0x" + "34" * 32), ("status", "0x2")]
)
def test_mismatched_receipt_is_rejected_over_http(field, value):
    tx_hash = "0x" + "12" * 32
    receipt = {"transactionHash": tx_hash, "status": "0x1"}
    receipt[field] = value

    class Endpoint:
        def on_post(self, req, resp):
            resp.media = {"jsonrpc": "2.0", "id": req.media["id"], "result": receipt}

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = falcon.App()
    app.add_route("/", Endpoint())
    server = http.Server(host="127.0.0.1", port=port, app=app)
    address = "0x" + "12" * 20
    effects = ContractEffects(
        rpc_url=f"http://127.0.0.1:{port}",
        chain_id=31337,
        writer=address,
        endorsement=address,
        identity=address,
    )
    completed = []

    def run(tymth, tock=0.0, **opts):
        yield tock
        with pytest.raises(ValueError, match="invalid transaction receipt"):
            yield from effects.receipt(tx_hash)
        completed.append(True)
        parent.remove([effects, server_doer])

    server_doer = http.ServerDoer(server=server)
    parent = doing.DoDoer(doers=[server_doer, effects, doing.doify(run)])
    doing.Doist(real=True, tock=0.01, limit=2).do(doers=[parent])
    assert completed


@pytest.mark.parametrize("unavailable", ["eth_getTransactionReceipt", None])
def test_rpc_outage_is_unknown_not_transaction_absence(tmp_path, unavailable):
    from vlei_oracle.persistence.basing import OracleBaser
    from vlei_oracle.persistence.records import TransactionRecord

    calls = []

    class Endpoint:
        def on_post(self, req, resp):
            method = req.media["method"]
            calls.append(method)
            if method == unavailable:
                resp.media = {
                    "jsonrpc": "2.0",
                    "id": req.media["id"],
                    "error": {"code": -32000, "message": "temporarily unavailable"},
                }
            else:
                resp.media = {
                    "jsonrpc": "2.0",
                    "id": req.media["id"],
                    "result": {
                        "eth_chainId": "0x7a69",
                        "eth_getTransactionReceipt": None,
                    }[method],
                }

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = falcon.App()
    app.add_route("/", Endpoint())
    server = http.Server(host="127.0.0.1", port=port, app=app)
    address = "0x" + "12" * 20
    effects = ContractEffects(
        rpc_url=f"http://127.0.0.1:{port}",
        chain_id=31337,
        writer=address,
        endorsement=address,
        identity=address,
    )
    store = OracleBaser(name="rpc-unknown", headDirPath=str(tmp_path))
    intent = persist_intent(
        store,
        TransactionRecord(
            txHash="0x" + "12" * 32,
            rawTransaction="0x1234",
            nonce=0,
            associationSaid="association",
            action="publishEndorsement",
        ),
    )
    completed = []

    def run(tymth, tock=0.0, **opts):
        yield tock
        if unavailable:
            with pytest.raises(ValueError, match="invalid RPC response"):
                yield from effects.receipt(intent.txHash)
        else:
            assert (yield from effects.receipt(intent.txHash)) is None
        assert pending_intent(store) == intent
        assert "eth_sendRawTransaction" not in calls
        completed.append(True)
        parent.remove([effects, server_doer])

    server_doer = http.ServerDoer(server=server)
    parent = doing.DoDoer(doers=[server_doer, effects, doing.doify(run)])
    try:
        doing.Doist(real=True, tock=0.01, limit=3).do(doers=[parent])
        assert completed
    finally:
        store.close(clear=True)

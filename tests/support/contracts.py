"""support/contracts.py."""

import json
from pathlib import Path

from eth_account import Account
from hio.base import doing
from web3 import Web3

from vlei_oracle.endorsement.effects import ContractEffects
from vlei_oracle.persistence.posting import PostingWriter
from vlei_oracle.persistence.records import TransactionRecord

KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

WALLET = Account.from_key("0x" + "0" * 63 + "1").address

IDENTITY = "0x" + "12" * 32


def contract(comply, name, address):
    """Read the actual compiled fixture ABI for local encoding only."""
    artifact = json.loads(
        (Path(comply) / "out" / (name + ".sol") / (name + ".json")).read_text()
    )
    return Web3().eth.contract(
        address=Web3.to_checksum_address(address), abi=artifact["abi"]
    )


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
    tx = {
        "from": account.address,
        "to": target.address,
        "value": 0,
        "data": target.encode_abi(function, args=list(args)),
    }
    nonce = yield from effects.request(
        "eth_getTransactionCount", [account.address, "pending"]
    )
    price = yield from effects.request("eth_gasPrice", [])
    gas = yield from effects.request("eth_estimateGas", [tx])
    tx.update(
        nonce=int(nonce, 16),
        gasPrice=int(price, 16),
        gas=int(gas, 16) + 10000,
        chainId=effects.chain_id,
    )
    signed = account.sign_transaction(tx)
    tx_hash = Web3.to_hex(signed.hash)
    yield from effects.submit(Web3.to_hex(signed.raw_transaction), tx_hash)
    while (receipt := (yield from effects.receipt(tx_hash))) is None:
        yield 0.01
    assert receipt["status"] == "0x1", receipt
    return receipt


def effects_for(deployment, *, chain_id=31337):
    rpc, addresses = deployment
    return ContractEffects(
        rpc_url=rpc,
        chain_id=chain_id,
        writer=Account.from_key(KEY).address,
        endorsement=addresses["oracleRegistry"],
        identity=addresses["identityRegistry"],
        confirmations=1,
    )


def drive(effects, workflow):
    """Run effect generators beside their HIO clients on one HIO clock."""
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


def signed_transaction(tx, *, said="association", functionName="publishEndorsement"):
    signed = Account.from_key(KEY).sign_transaction(tx)
    return TransactionRecord(
        txHash=Web3.to_hex(signed.hash),
        rawTransaction=Web3.to_hex(signed.raw_transaction),
        nonce=tx["nonce"],
        associationSaid=said,
        functionName=functionName,
    )


def observe(effects, wallet):
    """Independently inspect contract mappings for qualification assertions only."""
    yield from effects.check_chain()
    block = yield from effects.request("eth_getBlockByNumber", ["latest", False])
    selector = {"blockHash": block["hash"], "requireCanonical": True}
    values = []
    for key, function, types in (
        ("identity", "getIdentity", ["bytes32"]),
        ("endorsement", "getEndorsement", ["(bytes32,string)"]),
    ):
        contract = effects.contracts[key]
        data = contract.encode_abi(function, args=[Web3.to_checksum_address(wallet)])
        raw = yield from effects.request(
            "eth_call",
            [
                {"to": contract.address, "data": data},
                selector,
            ],
        )
        values.append(effects.web3.codec.decode(types, bytes.fromhex(raw[2:]))[0])
    identity, (entity, said) = values
    return "0x" + identity.hex(), ("0x" + entity.hex(), said)


def persist_transaction(store, txRecord):
    """Set up a signed pending phase through the same atomic transition as posting."""
    from vlei_oracle.persistence.records import PostingOperation

    postingOperation = PostingOperation(
        "0000000000000001",
        "event",
        txRecord.associationSaid,
        WALLET,
        functionName=txRecord.functionName,
        txHash=txRecord.txHash,
    )
    store.preparing.pin(keys=("writer",), val=postingOperation)
    PostingWriter(store).prep_to_pending(postingOperation, txRecord)
    return txRecord


def pending_transaction(store):
    """Read the pending operation's journal entry without history scans."""
    postingOperation = store.pending.get(keys=("writer",))
    return (
        store.transactions.get(keys=(postingOperation.txHash,))
        if postingOperation
        else None
    )

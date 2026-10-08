"""Contract encoding and native HIO JSON-RPC effects for one chain."""

import json
from importlib.resources import files

from hio.base import doing
from hio.core import http
from web3 import Web3

from .rpc import request_rpc


class ContractEffects(doing.DoDoer):
    """Own HTTP connection lifecycles; expose yielding chain operations.

    Web3 is used only for local ABI encoding/decoding. Every network wait uses
    a native HIO client. New connections isolate responses after a timeout.
    """

    def __init__(self, *, rpc_url, chain_id, writer, endorsement, identity,
                 confirmations=2, timeout=5.0):
        self.rpc_url, self.chain_id = rpc_url, chain_id
        self.writer = Web3.to_checksum_address(writer)
        self.confirmations, self.timeout = confirmations, timeout
        self.web3 = Web3()
        self.contracts = {}
        for key, address, filename in (
            ("endorsement", endorsement, "VLEIOracleRegistry.json"),
            ("identity", identity, "IdentityRegistry.json"),
        ):
            abi = json.loads(files("vlei_oracle").joinpath("contract_data", filename).read_text())
            self.contracts[key] = self.web3.eth.contract(
                address=Web3.to_checksum_address(address), abi=abi
            )
        super().__init__(doers=[], always=True)

    def request(self, method, params):
        """Own one isolated connection while its bounded response wait yields."""
        client = http.Client(path=self.rpc_url, reconnectable=True)
        doer = http.ClientDoer(client=client)
        self.extend([doer])
        try:
            return (yield from request_rpc(
                client, request_id=1, method=method, params=params,
                tymth=self.tymth, timeout=self.timeout, tock=self.tock,
            ))
        finally:
            self.remove([doer])

    def check_chain(self):
        """Refuse observations or effects from another chain."""
        chain = yield from self.request("eth_chainId", [])
        if int(chain, 16) != self.chain_id:
            raise ValueError("RPC chain does not match posting deployment")

    def observe(self, wallet):
        """Read both policy inputs at one identified canonical block."""
        yield from self.check_chain()
        block = yield from self.request("eth_getBlockByNumber", ["latest", False])
        selector = {"blockHash": block["hash"], "requireCanonical": True}
        values = []
        for key, function, types in (
            ("identity", "getIdentity", ["bytes32"]),
            ("endorsement", "getEndorsement", ["(bytes32,string)"]),
        ):
            contract = self.contracts[key]
            data = contract.encode_abi(function, args=[Web3.to_checksum_address(wallet)])
            raw = yield from self.request("eth_call", [
                {"to": contract.address, "data": data}, selector,
            ])
            values.append(self.web3.codec.decode(types, bytes.fromhex(raw[2:]))[0])
        identity, (entity, said) = values
        return "0x" + identity.hex(), ("0x" + entity.hex(), said)

    def prepare(self, action):
        """Fetch unsigned transaction inputs without broadcasting."""
        yield from self.check_chain()
        key, function, args = action
        contract = self.contracts[key]
        # ABI validation and encoding run locally against the actual pinned ABI.
        args = [Web3.to_checksum_address(arg) if isinstance(arg, str) and
                Web3.is_address(arg) else arg for arg in args]
        tx = {"from": self.writer, "to": contract.address,
              "data": contract.encode_abi(function, args=args), "value": 0}
        nonce = yield from self.request("eth_getTransactionCount", [self.writer, "pending"])
        price = yield from self.request("eth_gasPrice", [])
        gas = yield from self.request("eth_estimateGas", [tx])
        return {**tx, "nonce": int(nonce, 16), "gasPrice": int(price, 16),
                "gas": int(gas, 16) + 10000, "chainId": self.chain_id}

    def receipt(self, tx_hash):
        """Return only canonical receipt progress; orphaned receipts stay pending."""
        receipt = yield from self.request("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            return None
        if receipt["transactionHash"].lower() != tx_hash or receipt["status"] not in ("0x0", "0x1"):
            raise ValueError("RPC returned an invalid transaction receipt")
        block = yield from self.request("eth_getBlockByNumber", [receipt["blockNumber"], False])
        if block is None or block["hash"] != receipt["blockHash"]:
            return None
        head = yield from self.request("eth_blockNumber", [])
        receipt["confirmed"] = int(head, 16) - int(receipt["blockNumber"], 16) + 1 >= self.confirmations
        return receipt

    def reconcile(self, intent):
        """Observe a persisted identity before deciding whether to rebroadcast it."""
        yield from self.check_chain()
        for tx_hash in (intent.cancelHash, intent.txHash):
            if not tx_hash:
                continue
            receipt = yield from self.receipt(tx_hash)
            if receipt is not None:
                return "receipt", tx_hash, receipt
        tx_hash = intent.cancelHash or intent.txHash
        transaction = yield from self.request("eth_getTransactionByHash", [tx_hash])
        if transaction is not None:
            if (transaction["hash"].lower() != tx_hash or
                transaction["from"].lower() != self.writer.lower() or
                int(transaction["nonce"], 16) != intent.transaction["nonce"]):
                raise ValueError("RPC returned another transaction")
            if transaction["blockHash"] is None:
                return "pending", tx_hash, None
        nonce = yield from self.request("eth_getTransactionCount", [self.writer, "latest"])
        if int(nonce, 16) > intent.transaction["nonce"]:
            return "conflict", tx_hash, None
        return "absent", tx_hash, None

    def submit(self, raw, tx_hash):
        """Broadcast an already persisted signed identity; never allocate a nonce."""
        result = yield from self.request("eth_sendRawTransaction", [raw])
        if result.lower() != tx_hash:
            raise ValueError("RPC returned a different transaction identity")

    def cancellation(self, intent):
        """Build an explicit same-nonce cancellation of a stale signed action."""
        yield from self.check_chain()
        price = yield from self.request("eth_gasPrice", [])
        return {"from": self.writer, "to": self.writer, "value": 0, "gas": 21000,
                "nonce": intent.transaction["nonce"], "chainId": self.chain_id,
                "gasPrice": max(int(price, 16) * 2, intent.transaction["gasPrice"] * 2)}

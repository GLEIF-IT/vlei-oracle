"""Contract encoding and HIO JSON-RPC effects for one chain."""

import json
from importlib.resources import files

from hio.base import doing
from hio.core import http
from web3 import Web3

from vlei_oracle.endorsement.rpc import RpcClientDoer, request_rpc


class ContractEffects(doing.DoDoer):
    """Own HTTP connection lifecycles; expose yielding chain operations.

    Web3 is used only for local ABI encoding/decoding. Every network wait uses
    a HIO client. New connections isolate responses after a timeout.
    """

    def __init__(
        self,
        *,
        rpc_url,
        chain_id,
        writer,
        endorsement,
        identity,
        confirmations=2,
        timeout=5.0,
    ):
        self.rpc_url, self.chain_id = rpc_url, chain_id
        self.writer = Web3.to_checksum_address(writer)
        self.confirmations, self.timeout = confirmations, timeout
        self.web3 = Web3()
        self.contracts = {}
        for key, address, filename in (
            ("endorsement", endorsement, "VLEIOracleRegistry.json"),
            ("identity", identity, "IdentityRegistry.json"),
        ):
            abi = json.loads(
                files("vlei_oracle.endorsement")
                .joinpath("contract_data", filename)
                .read_text()
            )
            self.contracts[key] = self.web3.eth.contract(
                address=Web3.to_checksum_address(address), abi=abi
            )
        super().__init__(doers=[], always=True)

    def request(self, method, params):
        """Own one isolated connection while its bounded response wait yields."""
        client = http.Client(path=self.rpc_url, reconnectable=True)
        doer = RpcClientDoer(client=client)
        self.extend([doer])
        try:
            return (
                yield from request_rpc(
                    client,
                    request_id=1,
                    method=method,
                    params=params,
                    tymth=self.tymth,
                    timeout=self.timeout,
                    tock=self.tock,
                )
            )
        finally:
            self.remove([doer])

    def check_chain(self):
        """Refuse observations or effects from another chain."""
        chain = yield from self.request("eth_chainId", [])
        if int(chain, 16) != self.chain_id:
            raise ValueError("RPC chain does not match posting deployment")

    def prepare(self, action):
        """Fetch unsigned transaction inputs without broadcasting."""
        yield from self.check_chain()
        key, function, args = action
        contract = self.contracts[key]
        # ABI validation and encoding run locally against the actual pinned ABI.
        args = [
            (
                Web3.to_checksum_address(arg)
                if isinstance(arg, str) and Web3.is_address(arg)
                else arg
            )
            for arg in args
        ]
        tx = {
            "from": self.writer,
            "to": contract.address,
            "data": contract.encode_abi(function, args=args),
            "value": 0,
        }
        nonce = yield from self.request(
            "eth_getTransactionCount", [self.writer, "pending"]
        )
        price = yield from self.request("eth_gasPrice", [])
        gas = yield from self.request("eth_estimateGas", [tx])
        return {
            **tx,
            "nonce": int(nonce, 16),
            "gasPrice": int(price, 16),
            "gas": int(gas, 16) + 10000,
            "chainId": self.chain_id,
        }

    def receipt(self, tx_hash):
        """Return only canonical receipt progress; orphaned receipts stay pending."""
        receipt = yield from self.request("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            return None
        if receipt["transactionHash"].lower() != tx_hash or receipt["status"] not in (
            "0x0",
            "0x1",
        ):
            raise ValueError("RPC returned an invalid transaction receipt")
        block = yield from self.request(
            "eth_getBlockByNumber", [receipt["blockNumber"], False]
        )
        if block is None or block["hash"] != receipt["blockHash"]:
            return None
        head = yield from self.request("eth_blockNumber", [])
        receipt["confirmed"] = (
            int(head, 16) - int(receipt["blockNumber"], 16) + 1 >= self.confirmations
        )
        return receipt

    def submit(self, raw, tx_hash):
        """Broadcast an already persisted signed identity; never allocate a nonce."""
        result = yield from self.request("eth_sendRawTransaction", [raw])
        if result.lower() != tx_hash:
            raise ValueError("RPC returned a different transaction identity")

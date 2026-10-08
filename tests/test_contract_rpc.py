"""Malformed transaction observations must not establish confirmation."""

import socket

import falcon
from hio.base import doing
from hio.core import http
import pytest

from vlei_oracle.contracting import ContractEffects


@pytest.mark.parametrize("field,value", [("transactionHash", "0x" + "34" * 32), ("status", "0x2")])
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
        rpc_url=f"http://127.0.0.1:{port}", chain_id=31337,
        writer=address, endorsement=address, identity=address,
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

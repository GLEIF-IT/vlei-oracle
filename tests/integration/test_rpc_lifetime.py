"""Posting RPC failures preserve service progress and release LMDB readers."""

from dataclasses import replace

import pytest

from tests.support.contracts import KEY, drive, pending_transaction
from tests.support.posting import posting_service_for, settle
from tests.support.status_events import event_body, post, said
from vlei_oracle.endorsement.doing import PostingDoer
from vlei_oracle.persistence.associations import AssociationReader
from vlei_oracle.runtime.serving import StatusReceiver


@pytest.mark.parametrize("failure", ["tls", "chunk-size", "null", "missing-result"])
def test_bad_rpc_response_preserves_pending_transaction_and_signed_ack(
    peers, deployment, caplog, failure
):
    """RPC failures preserve a real signed transaction while the receiver ACKs duplicate delivery."""
    import json
    import socket

    from hio.base import doing
    from hio.core import http, tcp

    rpc_clients = []

    class BadResponsePeer(tcp.ServerDoer):
        """Reply over real TCP with the selected malformed RPC transport/envelope."""

        def recur(self, tyme):
            """Wait for a request or TLS hello, capture its client and send the bad response."""
            super().recur(tyme)
            for peer in self.server.ixes.values():
                if peer.rxbs:
                    rpc_clients.extend(
                        d.client
                        for d in postingService.effects.doers
                        if isinstance(d, http.ClientDoer)
                    )
                    peer.rxbs.clear()
                    if failure == "tls":
                        response = b"HTTP/1.1 400 Bad Request\r\n\r\n"
                    elif failure == "chunk-size":
                        response = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nZ\r\n"
                    else:
                        body = (
                            b"null"
                            if failure == "null"
                            else b'{"jsonrpc":"2.0","id":1}'
                        )
                        response = (
                            b"HTTP/1.1 200 OK\r\nContent-Length: "
                            + str(len(body)).encode()
                            + b"\r\n\r\n"
                            + body
                        )
                    peer.tx(response)

    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    server = tcp.Server(host="127.0.0.1", port=port)
    server_doer = BadResponsePeer(server=server)
    body = event_body(peers, "rpc-retry", 50)
    assert post(peers, body).status_code == 200
    initialPostingService = posting_service_for(peers, deployment)

    def submit(effects):
        """Create a real signed/submitted transaction, leaving transaction-receipt processing for a later cycle."""
        yield from initialPostingService.cycle()
        return True

    drive(initialPostingService.effects, submit)
    txRecord = pending_transaction(peers.inbox)
    assert txRecord is not None
    # Switch only the RPC peer; retain the deployment/writer and signed journal.
    _, addresses = deployment
    config = replace(
        peers.config,
        rpc_url=f"{'https' if failure == 'tls' else 'http'}://127.0.0.1:{server.ha[1]}",
        posting_key=KEY,
        endorsement_registry=addresses["oracleRegistry"],
        identity_registry=addresses["identityRegistry"],
    )
    receiver = StatusReceiver(config, hby=peers.oracle, baser=peers.inbox)
    from tests.support.oobis import resolve_peer

    resolve_peer(
        peers.oracle,
        config.verifier_oobi,
        peers.producer,
        peers.producer_port,
        resolver=receiver.oobiery,
    )
    postingService = next(
        d for d in receiver.doers if isinstance(d, PostingDoer)
    ).service
    client = http.Client(path=config.webhook_url)
    client_doer = http.ClientDoer(client=client)
    responses = []

    def probe(tymth, tock=0.0, **opts):
        """Wait for the posting error, then exercise status-event acceptance over a real signed connection."""
        yield tock
        while not any(
            "pending contract transaction failed:" in record.getMessage()
            for record in caplog.records
        ):
            yield 0.01
        headers = peers.auth.sign(
            method="POST",
            target=config.webhook_url,
            body=body,
        )
        client.request(method="POST", headers=headers, body=body)
        while not client.responses:
            yield 0.01
        response = client.responses.popleft()
        peers.auth.verify(
            method="POST",
            target=config.webhook_url,
            body=response["body"],
            headers=response["headers"],
        )
        responses.append(response)
        parent.remove([receiver, server_doer, client_doer])

    parent = doing.DoDoer(
        doers=[receiver, server_doer, client_doer, doing.doify(probe)]
    )
    doing.Doist(real=True, tock=0.01, limit=3).do(doers=[parent])
    assert parent.done and responses[0]["status"] == 200
    assert json.loads(responses[0]["body"])["stored"] is True
    assert (
        AssociationReader(peers.inbox).selected("eip155:31337:0x" + "11" * 20)
        is not None
    )
    assert rpc_clients and all(not client.connector.opened for client in rpc_clients)
    assert pending_transaction(peers.inbox) == txRecord
    assert (
        peers.inbox.transactions.cntAll() == 1
    )  # Failure cannot allocate another nonce.
    # Durable recovery still has exactly the original signed attempt after reopen.
    peers.inbox.close()
    peers.inbox.reopen()
    assert pending_transaction(peers.inbox) == txRecord


def test_rpc_yields_do_not_retain_lmdb_read_transactions(
    peers, deployment, monkeypatch
):
    """Actual HTTP RPC may suspend at any step without retaining a native reader."""
    from tests.support.readers import active_readers

    postingService = posting_service_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "reader-lifetime", 60)).status_code == 200
    request = postingService.effects.request
    yielded = []

    def checked_request(method, params):
        """Wrap the real client and check the actual reader table at each RPC yield."""
        rpcGen = request(method, params)
        try:
            while True:
                try:
                    delay = next(rpcGen)
                except StopIteration as completed:
                    return completed.value
                assert not active_readers(peers.inbox.env), peers.inbox.env.readers()
                yielded.append(method)
                yield delay
        finally:
            rpcGen.close()

    monkeypatch.setattr(postingService.effects, "request", checked_request)

    def workflow(effects):
        yield from settle(postingService, wallet, said("reader-lifetime"))
        return True

    drive(postingService.effects, workflow)
    assert {"eth_call", "eth_sendRawTransaction", "eth_getTransactionReceipt"} <= set(
        yielded
    )
    assert not active_readers(peers.inbox.env), peers.inbox.env.readers()

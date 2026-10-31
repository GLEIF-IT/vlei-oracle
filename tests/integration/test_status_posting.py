"""integration/test_status_posting.py."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.support.contracts import KEY, drive
from tests.support.status_events import event_body, post, said
from vlei_oracle.endorsement.posting import StatusPoster
from vlei_oracle.runtime.serving import StatusReceiver


def poster_for(peers, deployment):
    """Use the actual receiver composition, with a dedicated chain writer."""
    rpc, addresses = deployment
    config = replace(
        peers.config,
        rpc_url=rpc,
        posting_key=KEY,
        endorsement_registry=addresses["oracleRegistry"],
        identity_registry=addresses["identityRegistry"],
        confirmations=1,
    )
    receiver = StatusReceiver(config, hby=peers.oracle, baser=peers.inbox)
    return next(doer for doer in receiver.doers if isinstance(doer, StatusPoster))


def settle(poster, wallet, expected_said):
    """Drive the ordinary worker until both its receipt and actual mapping agree."""
    for _ in range(12):
        yield from poster.cycle()
        _, endorsement = yield from poster.effects.observe(wallet)
        if (
            endorsement[1] == expected_said
            and poster.baser.unfinished_transaction() is None
        ):
            return endorsement
    pytest.fail(f"mapping did not settle to {expected_said}")


def test_accepted_replaced_and_revoked_status_changes_real_mapping(peers, deployment):
    """Publish once, replace by issuance, ignore old revocation, then withdraw the exact current SAID."""
    poster = poster_for(peers, deployment)
    wallet = "0x" + "11" * 20
    older = event_body(peers, "published-old", 10)
    assert post(peers, older).status_code == 200

    def workflow(effects):
        # Fixture observation is dated 2000; no verifier process is running.
        endorsement = yield from settle(poster, wallet, said("published-old"))
        status = peers.inbox.statuses.get(keys=(said("published-old"),))
        assert endorsement[0] == status.identityRef
        count = peers.inbox.transactions.cntAll()
        assert post(peers, older).status_code == 200
        yield from poster.cycle()
        assert peers.inbox.transactions.cntAll() == count

        assert post(peers, event_body(peers, "published-new", 11)).status_code == 200
        yield from settle(poster, wallet, said("published-new"))
        assert (
            post(
                peers, event_body(peers, "published-old", 10, revoked=True)
            ).status_code
            == 200
        )
        yield from poster.cycle()
        assert (yield from effects.observe(wallet))[1][1] == said("published-new")
        assert (
            post(
                peers, event_body(peers, "published-new", 11, revoked=True)
            ).status_code
            == 200
        )
        yield from settle(poster, wallet, "")
        return True

    drive(poster.effects, workflow)


def test_pending_receipt_survives_restart_and_replacement(peers, deployment):
    """Submission is pending, not success; after restart finish it and apply the latest selected claim."""
    poster = poster_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "pending-old", 20)).status_code == 200

    def workflow(effects):
        # First establish the identity registry, then leave publication unmined.
        yield from poster.cycle()
        yield from poster.cycle()
        yield from effects.request("anvil_setAutomine", [False])
        try:
            yield from poster.cycle()
            intent = peers.inbox.unfinished_transaction()
            assert intent is not None
            yield from poster.cycle()
            assert peers.inbox.unfinished_transaction().txHash == intent.txHash
            assert (yield from effects.observe(wallet))[1][1] != said("pending-old")
            peers.inbox.close()
            peers.inbox.reopen()
            assert post(peers, event_body(peers, "pending-new", 21)).status_code == 200
            yield from effects.request("evm_mine", [])
            yield from poster.cycle()
            assert (
                peers.inbox.transactions.get(keys=(intent.txHash,)).state == "confirmed"
            )
        finally:
            yield from effects.request("anvil_setAutomine", [True])
        yield from settle(poster, wallet, said("pending-new"))
        return True

    drive(poster.effects, workflow)


def test_preparation_failure_retries_and_rechecks_replacement(peers, deployment):
    """A failed prepare sends nothing; a replacement during RPC suppresses the stale new send."""
    poster = poster_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "prepare-old", 30)).status_code == 200
    prepare = poster.effects.prepare

    def interrupted(action):
        tx = yield from prepare(action)
        assert post(peers, event_body(peers, "prepare-new", 31)).status_code == 200
        return tx

    def unavailable(action):
        yield 0.0
        raise TimeoutError("RPC unavailable")

    def workflow(effects):
        with patch.object(effects, "prepare", unavailable):
            with pytest.raises(TimeoutError, match="unavailable"):
                yield from poster.cycle()
        assert peers.inbox.transactions.cntAll() == 0
        with patch.object(effects, "prepare", interrupted):
            yield from poster.cycle()
        assert peers.inbox.transactions.cntAll() == 0
        yield from settle(poster, wallet, said("prepare-new"))
        return True

    drive(poster.effects, workflow)


def test_lost_broadcast_response_is_pending_until_confirmed(peers, deployment):
    """An uncertain send retains its hash and error across reopen and is never treated as delivered."""
    poster = poster_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "uncertain", 40)).status_code == 200
    submit = poster.effects.submit

    def lose_response(raw, tx_hash):
        yield from submit(raw, tx_hash)
        raise TimeoutError("lost response")

    def workflow(effects):
        with patch.object(effects, "submit", lose_response):
            with pytest.raises(TimeoutError):
                yield from poster.cycle()
        intent = peers.inbox.unfinished_transaction()
        assert intent.state == "pending" and "lost response" in intent.failureReason
        peers.inbox.close()
        peers.inbox.reopen()
        yield from poster.cycle()
        assert peers.inbox.transactions.get(keys=(intent.txHash,)).state == "confirmed"
        yield from settle(poster, wallet, said("uncertain"))
        return True

    drive(poster.effects, workflow)


@pytest.mark.parametrize("failure", ["tls", "chunk-size", "null", "missing-result"])
def test_bad_rpc_response_preserves_pending_intent_and_signed_ack(
    peers, deployment, caplog, failure
):
    """RPC failures preserve a real signed intent while the receiver ACKs duplicate delivery."""
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
                        for d in poster.effects.doers
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
    initial_poster = poster_for(peers, deployment)

    def submit(effects):
        """Create a real signed/submitted intent, leaving receipt processing for a later cycle."""
        yield from initial_poster.cycle()
        return True

    drive(initial_poster.effects, submit)
    intent = peers.inbox.unfinished_transaction()
    assert intent is not None
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
    poster = next(d for d in receiver.doers if isinstance(d, StatusPoster))
    client = http.Client(path=config.webhook_url)
    client_doer = http.ClientDoer(client=client)
    responses = []

    def probe(tymth, tock=0.0, **opts):
        """Wait for the posting error, then exercise receipt over a real signed connection."""
        yield tock
        while not any(
            "contract posting failed:" in record.getMessage()
            for record in caplog.records
        ):
            yield 0.01
        headers, sent = peers.auth.sign(
            method="POST",
            target=config.webhook_url,
            action="status",
            body=body,
        )
        client.request(method="POST", headers=headers, body=body)
        while not client.responses:
            yield 0.01
        response = client.responses.popleft()
        peers.auth.verify(
            method="POST",
            target=config.webhook_url,
            action="ack",
            body=response["body"],
            headers=response["headers"],
            request=sent.reference,
            status=response["status"],
        )
        responses.append(response)
        parent.remove([receiver, server_doer, client_doer])

    parent = doing.DoDoer(
        doers=[receiver, server_doer, client_doer, doing.doify(probe)]
    )
    doing.Doist(real=True, tock=0.01, limit=3).do(doers=[parent])
    assert parent.done and responses[0]["status"] == 200
    assert json.loads(responses[0]["body"])["stored"] is True
    assert peers.inbox.selected_association("eip155:31337:0x" + "11" * 20) is not None
    assert rpc_clients and all(not client.connector.opened for client in rpc_clients)
    assert peers.inbox.unfinished_transaction() == intent
    assert (
        peers.inbox.transactions.cntAll() == 1
    )  # Failure cannot allocate another nonce.
    # Durable recovery still has exactly the original signed attempt after reopen.
    peers.inbox.close()
    peers.inbox.reopen()
    assert peers.inbox.unfinished_transaction() == intent

"""integration/test_status_posting.py."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.support.contracts import KEY, drive, observe, pending_transaction
from tests.support.status_events import event_body, post, said
from vlei_oracle.endorsement.posting import StatusPoster
from vlei_oracle.endorsement.retrying import retry_operation
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
    for _ in range(36):
        yield from poster.cycle()
        _, endorsement = yield from observe(poster.effects, wallet)
        if (
            endorsement[1] == expected_said
            and pending_transaction(poster.baser) is None
            and poster.baser.preparing.get(keys=("writer",)) is None
            and poster.baser.queued.cntAll() == 0
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
        assert (yield from observe(effects, wallet))[1][1] == said("published-new")
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
        # Prepare, broadcast, then confirm identity registration.
        for _ in range(3):
            yield from poster.cycle()
        yield from effects.request("anvil_setAutomine", [False])
        try:
            yield from poster.cycle()
            txRecord = pending_transaction(peers.inbox)
            assert txRecord is not None
            yield from poster.cycle()
            assert pending_transaction(peers.inbox).txHash == txRecord.txHash
            assert (yield from observe(effects, wallet))[1][1] != said("pending-old")
            peers.inbox.close()
            peers.inbox.reopen()
            assert post(peers, event_body(peers, "pending-new", 21)).status_code == 200
            yield from effects.request("evm_mine", [])
            yield from poster.cycle()
            assert (
                peers.inbox.transactions.get(keys=(txRecord.txHash,)).state
                == "confirmed"
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

    def interrupted(contractCall):
        tx = yield from prepare(contractCall)
        assert post(peers, event_body(peers, "prepare-new", 31)).status_code == 200
        return tx

    def unavailable(contractCall):
        yield 0.0
        raise TimeoutError("RPC unavailable")

    def workflow(effects):
        with patch.object(effects, "prepare", unavailable):
            yield from poster.cycle()
            assert "unavailable" in peers.inbox.preparing.get(keys=("writer",)).error
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
            yield from poster.cycle()  # Persist the signature.
            yield from poster.cycle()  # Broadcast, then lose its response.
        txRecord = pending_transaction(peers.inbox)
        assert (
            txRecord.state == "pending"
            and "lost response" in peers.inbox.pending.get(keys=("writer",)).error
        )
        peers.inbox.close()
        peers.inbox.reopen()
        yield from poster.cycle()
        assert (
            peers.inbox.transactions.get(keys=(txRecord.txHash,)).state == "confirmed"
        )
        yield from settle(poster, wallet, said("uncertain"))
        return True

    drive(poster.effects, workflow)


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
        """Create a real signed/submitted transaction, leaving receipt processing for a later cycle."""
        yield from initial_poster.cycle()
        return True

    drive(initial_poster.effects, submit)
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
    poster = next(d for d in receiver.doers if isinstance(d, StatusPoster))
    client = http.Client(path=config.webhook_url)
    client_doer = http.ClientDoer(client=client)
    responses = []

    def probe(tymth, tock=0.0, **opts):
        """Wait for the posting error, then exercise receipt over a real signed connection."""
        yield tock
        while not any(
            "pending contract transaction failed:" in record.getMessage()
            for record in caplog.records
        ):
            yield 0.01
        headers, requestAuth = peers.auth.sign(
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
            request=requestAuth.reference,
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

    poster = poster_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "reader-lifetime", 60)).status_code == 200
    request = poster.effects.request
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

    monkeypatch.setattr(poster.effects, "request", checked_request)

    def workflow(effects):
        yield from settle(poster, wallet, said("reader-lifetime"))
        return True

    drive(poster.effects, workflow)
    assert {"eth_call", "eth_sendRawTransaction", "eth_getTransactionReceipt"} <= set(
        yielded
    )
    assert not active_readers(peers.inbox.env), peers.inbox.env.readers()


def test_completed_delivery_is_idle_even_after_external_chain_change(peers, deployment):
    """Completion removes work; external drift does not create a new obligation."""
    from falcon import testing

    from tests.support.contracts import send

    poster = poster_for(peers, deployment)
    wallet = "0x" + "11" * 20
    association = said("idle")
    assert post(peers, event_body(peers, "idle", 70)).status_code == 200
    client = testing.TestClient(peers.receiver.app)
    assert (
        client.simulate_get(f"/v1/endorsements/{association}").json["phase"] == "queued"
    )
    assert client.simulate_get("/v1/endorsements/unknown").status_code == 404

    def workflow(effects):
        yield from settle(poster, wallet, association)
        response = client.simulate_get(f"/v1/endorsements/{association}")
        assert response.json["phase"] == "completed"
        assert response.json["operation"]["functionName"] == "publishEndorsement"
        assert "action" not in response.json["operation"]
        assert response.json["confirmedEffect"]["associationSaid"] == association
        yield from send(
            effects,
            effects.contracts["endorsement"],
            "withdrawEndorsement",
            [wallet, association],
            KEY,
        )
        with patch.object(effects, "request", side_effect=AssertionError("idle RPC")):
            for _ in range(5):
                yield from poster.cycle()
            # Reading REST status is equally passive, even with no reachable RPC.
            assert (
                client.simulate_get(f"/v1/endorsements/{association}").json
                == response.json
            )
        assert (yield from observe(effects, wallet))[1][1] == ""
        return True

    drive(poster.effects, workflow)


def test_reverts_exhaust_budget_then_require_explicit_retry(peers, deployment):
    """Real mined reverts become one dead letter; correction can resume the operation."""
    from falcon import testing

    poster = poster_for(peers, deployment)
    association = said("revert-budget")
    assert post(peers, event_body(peers, "revert-budget", 80)).status_code == 200
    prepare = poster.effects.prepare

    def insufficient_execution_gas(contractCall):
        """Enough intrinsic gas to mine, too little to register the identity."""
        tx = yield from prepare(contractCall)
        tx["gas"] = 40000
        return tx

    def workflow(effects):
        with patch.object(effects, "prepare", insufficient_execution_gas):
            for _ in range(10):
                yield from poster.cycle()
        state = (
            testing.TestClient(peers.receiver.app)
            .simulate_get(f"/v1/endorsements/{association}")
            .json
        )
        assert state["phase"] == "dead" and state["operation"]["attempts"] == 3
        assert "reverted" in state["operation"]["error"]
        assert state["verifierStatus"] == "accepted"
        assert state["confirmedEffect"]["identityRef"] == ""
        assert peers.inbox.transactions.cntAll() == 3
        with patch.object(
            effects, "request", side_effect=AssertionError("dead letter retried")
        ):
            yield from poster.cycle()
        peers.inbox.close()
        peers.inbox.reopen()
        retry_operation(peers.inbox, state["operation"]["operationId"])
        yield from settle(poster, "0x" + "11" * 20, association)
        return True

    drive(poster.effects, workflow)


def test_receipt_deadline_fences_writer_and_manual_retry_preserves_signature(
    peers, deployment
):
    """A timed-out signed operation must resolve before any later nonce is allocated."""
    poster = poster_for(peers, deployment)
    now = [1000.0]
    poster.clock = lambda: now[0]
    assert post(peers, event_body(peers, "deadline", 90)).status_code == 200

    def workflow(effects):
        yield from effects.request("anvil_setAutomine", [False])
        try:
            yield from poster.cycle()  # Signature is durable, before broadcast.
            txRecord = pending_transaction(peers.inbox)
            yield from poster.cycle()  # Submit without mining.
            now[0] += 121
            yield from poster.cycle()
            phase, postingOperation = peers.inbox.operation_for(f"{said('deadline')}:1")
            assert phase == "dead" and postingOperation.txHash == txRecord.txHash
            assert (
                post(peers, event_body(peers, "after-deadline", 91)).status_code == 200
            )
            with patch.object(
                effects, "request", side_effect=AssertionError("fence bypassed")
            ):
                yield from poster.cycle()
            assert peers.inbox.transactions.cntAll() == 1
            peers.inbox.close()
            peers.inbox.reopen()
            retry_operation(peers.inbox, postingOperation.operationId)
            assert pending_transaction(peers.inbox) == txRecord
            yield from effects.request("evm_mine", [])
            yield from poster.cycle()
            assert (
                peers.inbox.transactions.get(keys=(txRecord.txHash,)).state
                == "confirmed"
            )
        finally:
            yield from effects.request("anvil_setAutomine", [True])
        yield from settle(poster, "0x" + "11" * 20, said("after-deadline"))
        return True

    drive(poster.effects, workflow)


def test_revocation_during_pending_publication_is_followed_by_withdrawal(
    peers, deployment
):
    """Resolve the signed publish, then honor the new off-chain revocation in order."""
    poster = poster_for(peers, deployment)
    assert post(peers, event_body(peers, "retire-pending", 100)).status_code == 200

    def workflow(effects):
        for _ in range(3):
            yield from poster.cycle()  # Confirm identity.
        yield from effects.request("anvil_setAutomine", [False])
        try:
            yield from poster.cycle()
            yield from poster.cycle()  # Publication is now in the mempool.
            txRecord = pending_transaction(peers.inbox)
            assert txRecord.functionName == "publishEndorsement"
            assert (
                post(
                    peers, event_body(peers, "retire-pending", 100, revoked=True)
                ).status_code
                == 200
            )
            yield from effects.request("evm_mine", [])
            yield from poster.cycle()
        finally:
            yield from effects.request("anvil_setAutomine", [True])
        yield from settle(poster, "0x" + "11" * 20, "")
        assert (
            peers.inbox.walletEffects.get(keys=("0x" + "11" * 20,)).associationSaid
            == ""
        )
        return True

    drive(poster.effects, workflow)


def test_newer_retirement_withdraws_published_predecessor(peers, deployment):
    """A retirement received before its acceptance still releases the older claim."""
    poster = poster_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "predecessor", 110)).status_code == 200

    def workflow(effects):
        yield from settle(poster, wallet, said("predecessor"))
        assert (
            post(
                peers, event_body(peers, "already-retired", 111, revoked=True)
            ).status_code
            == 200
        )
        yield from settle(poster, wallet, "")
        assert peers.inbox.selected_association("eip155:31337:" + wallet) is None
        return True

    drive(poster.effects, workflow)


def test_preparation_errors_stop_in_dead_letter(peers, deployment):
    """Unsigned RPC failure has a finite durable budget and never invalidates a claim."""
    from falcon import testing

    poster = poster_for(peers, deployment)
    association = said("prepare-exhausted")
    assert post(peers, event_body(peers, "prepare-exhausted", 120)).status_code == 200

    def unavailable(contractCall):
        yield 0.0
        raise TimeoutError("preparation unavailable")

    def workflow(effects):
        with patch.object(effects, "prepare", unavailable):
            for attempt in range(3):
                yield from poster.cycle()
                state = (
                    testing.TestClient(peers.receiver.app)
                    .simulate_get(f"/v1/endorsements/{association}")
                    .json
                )
                assert state["operation"]["attempts"] == attempt + 1
                assert state["phase"] == ("dead" if attempt == 2 else "preparing")
                assert "unavailable" in state["operation"]["error"]
        assert (
            state["verifierStatus"] == "accepted" and state["selection"] == "selected"
        )
        assert peers.inbox.transactions.cntAll() == 0
        with patch.object(
            effects, "request", side_effect=AssertionError("automatic retry")
        ):
            yield from poster.cycle()
        return True

    drive(poster.effects, workflow)


def test_pending_rpc_errors_keep_signed_dead_letter(peers, deployment):
    """Repeated failed receipt RPCs stop, retaining the unresolved nonce across reopen."""
    poster = poster_for(peers, deployment)
    assert post(peers, event_body(peers, "pending-exhausted", 130)).status_code == 200

    def unavailable(tx_hash):
        yield 0.0
        raise TimeoutError("receipt unavailable")

    def workflow(effects):
        yield from poster.cycle()
        txRecord = pending_transaction(peers.inbox)
        with patch.object(effects, "receipt", unavailable):
            for _ in range(3):
                yield from poster.cycle()
        peers.inbox.close()
        peers.inbox.reopen()
        phase, postingOperation = peers.inbox.operation_for(
            f"{said('pending-exhausted')}:1"
        )
        assert phase == "dead" and postingOperation.rpcFailures == 3
        assert postingOperation.txHash == txRecord.txHash
        assert peers.inbox.transactions.get(keys=(txRecord.txHash,)) == txRecord
        with patch.object(
            effects, "request", side_effect=AssertionError("automatic retry")
        ):
            yield from poster.cycle()
        return True

    drive(poster.effects, workflow)

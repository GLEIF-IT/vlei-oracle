"""integration/test_status_posting.py."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.support.contracts import KEY, drive, observe, pending_transaction
from tests.support.status_events import event_body, post, said
from vlei_oracle.endorsement.doing import PostingDoer
from vlei_oracle.endorsement.retrying import RecoveryService
from vlei_oracle.persistence.associations import AssociationReader
from vlei_oracle.persistence.posting import PostingReader, PostingWriter
from vlei_oracle.runtime.serving import StatusReceiver


def posting_service_for(peers, deployment):
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
    return next(
        doer for doer in receiver.doers if isinstance(doer, PostingDoer)
    ).service


def settle(postingService, wallet, expected_said):
    """Drive the ordinary worker until both its transaction receipt and actual mapping agree."""
    for _ in range(36):
        yield from postingService.cycle()
        _, endorsement = yield from observe(postingService.effects, wallet)
        if (
            endorsement[1] == expected_said
            and postingService.reader.next_pending() is None
            and postingService.reader.next_prep() is None
            and postingService.reader.next_queued() is None
        ):
            return endorsement
    pytest.fail(f"mapping did not settle to {expected_said}")


def test_accepted_replaced_and_revoked_status_changes_real_mapping(peers, deployment):
    """Publish once, replace by issuance, ignore old revocation, then withdraw the exact current SAID."""
    postingService = posting_service_for(peers, deployment)
    wallet = "0x" + "11" * 20
    from falcon import testing

    from vlei_oracle.persistence.records import PostingSequence

    client = testing.TestClient(peers.receiver.app)

    def endorsement_response(label):
        return client.simulate_get(f"/v1/endorsements/{said(label)}").json

    older = event_body(peers, "published-old", 10)
    assert post(peers, older).status_code == 200

    def workflow(effects):
        # Fixture observation is dated 2000; no verifier process is running.
        endorsement = yield from settle(postingService, wallet, said("published-old"))
        status = peers.inbox.statuses.get(keys=(said("published-old"),))
        assert endorsement[0] == status.identityRef
        count = peers.inbox.transactions.cntAll()
        assert post(peers, older).status_code == 200
        yield from postingService.cycle()
        assert peers.inbox.transactions.cntAll() == count

        assert post(peers, event_body(peers, "published-new", 11)).status_code == 200
        yield from settle(postingService, wallet, said("published-new"))
        predecessor = endorsement_response("published-old")
        successor = endorsement_response("published-new")
        assert predecessor["publicationStatus"] == "not_published"
        assert predecessor["deliveryStatus"] == "superseded"
        assert successor["publicationStatus"] == "published"
        assert successor["deliveryStatus"] == "completed"
        assert (
            set(predecessor)
            == set(successor)
            == {
                "associationSaid",
                "wallet",
                "publicationStatus",
                "action",
                "deliveryStatus",
                "error",
            }
        )
        # A global unresolved transaction must not alter either association's response.
        sequence = peers.inbox.sequence.get(keys=("writer",))
        peers.inbox.sequence.pin(
            keys=("writer",), val=PostingSequence(sequence.next, "unrelated")
        )
        try:
            assert endorsement_response("published-old") == predecessor
            assert endorsement_response("published-new") == successor
        finally:
            peers.inbox.sequence.pin(keys=("writer",), val=sequence)

        assert (
            post(
                peers, event_body(peers, "published-old", 10, revoked=True)
            ).status_code
            == 200
        )
        yield from postingService.cycle()
        assert (yield from observe(effects, wallet))[1][1] == said("published-new")
        assert (
            post(
                peers, event_body(peers, "published-new", 11, revoked=True)
            ).status_code
            == 200
        )
        withdrawing = endorsement_response("published-new")
        assert withdrawing["action"] == "withdraw"
        assert withdrawing["deliveryStatus"] == "queued"
        assert withdrawing["publicationStatus"] == "published"
        yield from settle(postingService, wallet, "")
        withdrawn = endorsement_response("published-new")
        assert withdrawn["action"] == "withdraw"
        assert withdrawn["deliveryStatus"] == "completed"
        assert withdrawn["publicationStatus"] == "not_published"
        return True

    drive(postingService.effects, workflow)


def test_pending_tx_receipt_survives_restart_and_replacement(peers, deployment):
    """Submission is pending, not success; after restart finish it and apply the latest selected claim."""
    postingService = posting_service_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "pending-old", 20)).status_code == 200

    def workflow(effects):
        # First establish the identity registry, then leave publication unmined.
        # Prepare, broadcast, then confirm identity registration.
        for _ in range(3):
            yield from postingService.cycle()
        yield from effects.request("anvil_setAutomine", [False])
        try:
            yield from postingService.cycle()
            txRecord = pending_transaction(peers.inbox)
            assert txRecord is not None
            yield from postingService.cycle()
            assert pending_transaction(peers.inbox).txHash == txRecord.txHash
            assert (yield from observe(effects, wallet))[1][1] != said("pending-old")
            peers.inbox.close()
            peers.inbox.reopen()
            assert post(peers, event_body(peers, "pending-new", 21)).status_code == 200
            yield from effects.request("evm_mine", [])
            yield from postingService.cycle()
            assert (
                peers.inbox.transactions.get(keys=(txRecord.txHash,)).state
                == "confirmed"
            )
        finally:
            yield from effects.request("anvil_setAutomine", [True])
        yield from settle(postingService, wallet, said("pending-new"))
        return True

    drive(postingService.effects, workflow)


def test_preparation_failure_retries_and_rechecks_replacement(peers, deployment):
    """A failed prepare sends nothing; a replacement during RPC suppresses the stale new send."""
    postingService = posting_service_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "prepare-old", 30)).status_code == 200
    prepare = postingService.effects.prepare

    def interrupted(contractCall):
        tx = yield from prepare(contractCall)
        assert post(peers, event_body(peers, "prepare-new", 31)).status_code == 200
        return tx

    def unavailable(contractCall):
        yield 0.0
        raise TimeoutError("RPC unavailable")

    def workflow(effects):
        with patch.object(effects, "prepare", unavailable):
            yield from postingService.cycle()
            assert "unavailable" in peers.inbox.preparing.get(keys=("writer",)).error
        assert peers.inbox.transactions.cntAll() == 0
        with patch.object(effects, "prepare", interrupted):
            yield from postingService.cycle()
        assert peers.inbox.transactions.cntAll() == 0
        yield from settle(postingService, wallet, said("prepare-new"))
        return True

    drive(postingService.effects, workflow)


def test_lost_broadcast_response_is_pending_until_confirmed(peers, deployment):
    """An uncertain send retains its hash and error across reopen and is never treated as delivered."""
    postingService = posting_service_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "uncertain", 40)).status_code == 200
    submit = postingService.effects.submit

    def lose_response(raw, tx_hash):
        yield from submit(raw, tx_hash)
        raise TimeoutError("lost response")

    def workflow(effects):
        with patch.object(effects, "submit", lose_response):
            yield from postingService.cycle()  # Persist the signature.
            yield from postingService.cycle()  # Broadcast, then lose its response.
        txRecord = pending_transaction(peers.inbox)
        assert (
            txRecord.state == "pending"
            and "lost response" in peers.inbox.pending.get(keys=("writer",)).error
        )
        peers.inbox.close()
        peers.inbox.reopen()
        yield from postingService.cycle()
        assert (
            peers.inbox.transactions.get(keys=(txRecord.txHash,)).state == "confirmed"
        )
        yield from settle(postingService, wallet, said("uncertain"))
        return True

    drive(postingService.effects, workflow)


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


def test_completed_delivery_is_idle_even_after_external_chain_change(peers, deployment):
    """Completion removes work; external drift does not create a new obligation."""
    from falcon import testing

    from tests.support.contracts import send

    postingService = posting_service_for(peers, deployment)
    wallet = "0x" + "11" * 20
    association = said("idle")
    assert post(peers, event_body(peers, "idle", 70)).status_code == 200
    client = testing.TestClient(peers.receiver.app)
    assert (
        client.simulate_get(f"/v1/endorsements/{association}").json["deliveryStatus"]
        == "queued"
    )
    assert client.simulate_get("/v1/endorsements/unknown").status_code == 404

    def workflow(effects):
        yield from settle(postingService, wallet, association)
        response = client.simulate_get(f"/v1/endorsements/{association}")
        assert response.json == {
            "associationSaid": association,
            "wallet": wallet,
            "publicationStatus": "published",
            "action": "publish",
            "deliveryStatus": "completed",
            "error": "",
        }
        yield from send(
            effects,
            effects.contracts["endorsement"],
            "withdrawEndorsement",
            [wallet, association],
            KEY,
        )
        with patch.object(effects, "request", side_effect=AssertionError("idle RPC")):
            for _ in range(5):
                yield from postingService.cycle()
            # Reading REST status is equally passive, even with no reachable RPC.
            assert (
                client.simulate_get(f"/v1/endorsements/{association}").json
                == response.json
            )
        assert (yield from observe(effects, wallet))[1][1] == ""
        return True

    drive(postingService.effects, workflow)


def test_reverts_exhaust_budget_then_require_explicit_retry(peers, deployment):
    """Real mined reverts become one dead letter; correction can resume the operation."""
    from falcon import testing

    postingService = posting_service_for(peers, deployment)
    association = said("revert-budget")
    assert post(peers, event_body(peers, "revert-budget", 80)).status_code == 200
    prepare = postingService.effects.prepare

    def insufficient_execution_gas(contractCall):
        """Enough intrinsic gas to mine, too little to register the identity."""
        tx = yield from prepare(contractCall)
        tx["gas"] = 40000
        return tx

    def workflow(effects):
        with patch.object(effects, "prepare", insufficient_execution_gas):
            for _ in range(10):
                yield from postingService.cycle()
        state = (
            testing.TestClient(peers.receiver.app)
            .simulate_get(f"/v1/endorsements/{association}")
            .json
        )
        phase, postingOperation = PostingReader(peers.inbox).operation_for(
            f"{association}:1"
        )
        assert phase == "dead" and postingOperation.attempts == 3
        assert state["deliveryStatus"] == "failed"
        assert "reverted" in state["error"]
        assert state["action"] == "publish"
        assert state["publicationStatus"] == "not_published"
        assert peers.inbox.statuses.get(keys=(association,)).status == "accepted"
        assert PostingReader(peers.inbox).effects("0x" + "11" * 20) is None
        assert peers.inbox.transactions.cntAll() == 3
        with patch.object(
            effects, "request", side_effect=AssertionError("dead letter retried")
        ):
            yield from postingService.cycle()
        peers.inbox.close()
        peers.inbox.reopen()
        RecoveryService(
            PostingReader(peers.inbox), PostingWriter(peers.inbox)
        ).retry_operation(postingOperation.operationId)
        yield from settle(postingService, "0x" + "11" * 20, association)
        return True

    drive(postingService.effects, workflow)


def test_tx_receipt_deadline_fences_writer_and_manual_retry_preserves_signature(
    peers, deployment
):
    """A timed-out signed operation must resolve before any later nonce is allocated."""
    postingService = posting_service_for(peers, deployment)
    now = [1000.0]
    postingService.clock = lambda: now[0]
    assert post(peers, event_body(peers, "deadline", 90)).status_code == 200

    def workflow(effects):
        yield from effects.request("anvil_setAutomine", [False])
        try:
            yield from postingService.cycle()  # Signature is durable, before broadcast.
            txRecord = pending_transaction(peers.inbox)
            yield from postingService.cycle()  # Submit without mining.
            now[0] += 121
            yield from postingService.cycle()
            phase, postingOperation = PostingReader(peers.inbox).operation_for(
                f"{said('deadline')}:1"
            )
            assert phase == "dead" and postingOperation.txHash == txRecord.txHash
            assert (
                post(peers, event_body(peers, "after-deadline", 91)).status_code == 200
            )
            with patch.object(
                effects, "request", side_effect=AssertionError("fence bypassed")
            ):
                yield from postingService.cycle()
            assert peers.inbox.transactions.cntAll() == 1
            peers.inbox.close()
            peers.inbox.reopen()
            RecoveryService(
                PostingReader(peers.inbox), PostingWriter(peers.inbox)
            ).retry_operation(postingOperation.operationId)
            assert pending_transaction(peers.inbox) == txRecord
            yield from effects.request("evm_mine", [])
            yield from postingService.cycle()
            assert (
                peers.inbox.transactions.get(keys=(txRecord.txHash,)).state
                == "confirmed"
            )
        finally:
            yield from effects.request("anvil_setAutomine", [True])
        yield from settle(postingService, "0x" + "11" * 20, said("after-deadline"))
        return True

    drive(postingService.effects, workflow)


def test_revocation_during_pending_publication_is_followed_by_withdrawal(
    peers, deployment
):
    """Resolve the signed publish, then honor the new off-chain revocation in order."""
    postingService = posting_service_for(peers, deployment)
    assert post(peers, event_body(peers, "revocation-pending", 100)).status_code == 200

    def workflow(effects):
        for _ in range(3):
            yield from postingService.cycle()  # Confirm identity.
        yield from effects.request("anvil_setAutomine", [False])
        try:
            yield from postingService.cycle()
            yield from postingService.cycle()  # Publication is now in the mempool.
            txRecord = pending_transaction(peers.inbox)
            assert txRecord.functionName == "publishEndorsement"
            assert (
                post(
                    peers, event_body(peers, "revocation-pending", 100, revoked=True)
                ).status_code
                == 200
            )
            yield from effects.request("evm_mine", [])
            yield from postingService.cycle()
        finally:
            yield from effects.request("anvil_setAutomine", [True])
        yield from settle(postingService, "0x" + "11" * 20, "")
        assert (
            peers.inbox.walletEffects.get(keys=("0x" + "11" * 20,)).associationSaid
            == ""
        )
        return True

    drive(postingService.effects, workflow)


def test_newer_revocation_withdraws_published_predecessor(peers, deployment):
    """A revocation received before its acceptance still releases the older claim."""
    postingService = posting_service_for(peers, deployment)
    wallet = "0x" + "11" * 20
    assert post(peers, event_body(peers, "predecessor", 110)).status_code == 200

    def workflow(effects):
        yield from settle(postingService, wallet, said("predecessor"))
        assert (
            post(
                peers, event_body(peers, "already-revoked", 111, revoked=True)
            ).status_code
            == 200
        )
        yield from settle(postingService, wallet, "")
        assert AssociationReader(peers.inbox).selected("eip155:31337:" + wallet) is None
        return True

    drive(postingService.effects, workflow)


def test_preparation_errors_stop_in_dead_letter(peers, deployment):
    """Unsigned RPC failure has a finite durable budget and never invalidates a claim."""
    from falcon import testing

    postingService = posting_service_for(peers, deployment)
    association = said("prepare-exhausted")
    assert post(peers, event_body(peers, "prepare-exhausted", 120)).status_code == 200

    def unavailable(contractCall):
        yield 0.0
        raise TimeoutError("preparation unavailable")

    def workflow(effects):
        with patch.object(effects, "prepare", unavailable):
            for attempt in range(3):
                yield from postingService.cycle()
                state = (
                    testing.TestClient(peers.receiver.app)
                    .simulate_get(f"/v1/endorsements/{association}")
                    .json
                )
                phase, postingOperation = PostingReader(peers.inbox).operation_for(
                    f"{association}:1"
                )
                assert postingOperation.attempts == attempt + 1
                assert phase == ("dead" if attempt == 2 else "preparing")
                assert state["deliveryStatus"] == (
                    "failed" if attempt == 2 else "in_progress"
                )
                assert "unavailable" in state["error"]
        associationStatus = AssociationReader(peers.inbox).association(association)
        assert (
            associationStatus.status == "accepted"
            and associationStatus.selection == "selected"
        )
        assert (
            state["action"] == "publish"
            and state["publicationStatus"] == "not_published"
        )
        assert peers.inbox.transactions.cntAll() == 0
        with patch.object(
            effects, "request", side_effect=AssertionError("automatic retry")
        ):
            yield from postingService.cycle()
        return True

    drive(postingService.effects, workflow)


def test_pending_rpc_errors_keep_signed_dead_letter(peers, deployment):
    """Repeated failed transaction-receipt RPCs stop, retaining the unresolved nonce across reopen."""
    postingService = posting_service_for(peers, deployment)
    assert post(peers, event_body(peers, "pending-exhausted", 130)).status_code == 200

    def unavailable(tx_hash):
        yield 0.0
        raise TimeoutError("transaction receipt unavailable")

    def workflow(effects):
        yield from postingService.cycle()
        txRecord = pending_transaction(peers.inbox)
        with patch.object(effects, "transaction_receipt", unavailable):
            for _ in range(3):
                yield from postingService.cycle()
        peers.inbox.close()
        peers.inbox.reopen()
        phase, postingOperation = PostingReader(peers.inbox).operation_for(
            f"{said('pending-exhausted')}:1"
        )
        assert phase == "dead" and postingOperation.rpcFailures == 3
        assert postingOperation.txHash == txRecord.txHash
        assert peers.inbox.transactions.get(keys=(txRecord.txHash,)) == txRecord
        with patch.object(
            effects, "request", side_effect=AssertionError("automatic retry")
        ):
            yield from postingService.cycle()
        return True

    drive(postingService.effects, workflow)

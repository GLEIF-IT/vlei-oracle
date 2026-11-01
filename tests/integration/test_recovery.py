"""Posting restart, ambiguous submission, bounded retries and manual recovery."""

from unittest.mock import patch

from tests.support.contracts import drive, observe, pending_transaction
from tests.support.posting import posting_service_for, settle
from tests.support.status_events import event_body, post, said
from vlei_oracle.endorsement.retrying import RecoveryService
from vlei_oracle.persistence.associations import AssociationReader
from vlei_oracle.persistence.posting import PostingReader, PostingWriter


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

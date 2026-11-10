"""Publication, replacement and exact association withdrawal against real contracts."""

from unittest.mock import patch

import pytest

from tests.support.contracts import (
    KEY,
    drive,
    observe,
    pending_transaction,
    send,
    withdrawal_logs,
)
from tests.support.posting import posting_service_for, settle
from tests.support.status_events import event_body, post, said
from vlei_oracle.endorsement.posting import PostingService
from vlei_oracle.endorsement.retrying import RecoveryService
from vlei_oracle.persistence.associations import AssociationReader
from vlei_oracle.persistence.posting import PostingReader, PostingWriter


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


WALLETS = {n: "0x" + f"{n}{n}" * 20 for n in (1, 2, 3)}


def drain(postingService, limit=80):
    """Run the ordinary worker until no queued, preparing or pending work remains."""
    for _ in range(limit):
        yield from postingService.cycle()
        reader = postingService.reader
        if (
            reader.next_pending() is None
            and reader.next_prep() is None
            and reader.next_queued() is None
        ):
            return
    pytest.fail("posting work did not drain")


def withdrawals(store):
    """Return journaled withdrawal transactions in nonce order."""
    return sorted(
        (
            record
            for _, record in store.transactions.getItemIter()
            if record.functionName == "withdrawEndorsement"
        ),
        key=lambda record: record.nonce,
    )


def test_revoked_burst_withdraws_only_selected_wallets_and_resumes_after_restart(
    peers, deployment
):
    """Three published wallets, three revocations, one unselected: two serial withdrawals."""
    postingService = posting_service_for(peers, deployment)
    rival = peers.receiver.hab.pre  # A distinct AID standing in for another LE's QVI.
    for number in (1, 2, 3):
        assert (
            post(
                peers,
                event_body(peers, f"burst-{number}", 10, wallet=WALLETS[number]),
            ).status_code
            == 200
        )
    # Wallet 3 also has a competing claim; it is never selected and never published.
    assert (
        post(
            peers, event_body(peers, "burst-rival", 900, issuer=rival, wallet=WALLETS[3])
        ).status_code
        == 200
    )
    assert peers.inbox.statuses.get(keys=(said("burst-rival"),)).selection == "rejected"

    def workflow(effects):
        yield from drain(postingService)
        for number in (1, 2, 3):
            assert (yield from observe(effects, WALLETS[number]))[1][1] == said(
                f"burst-{number}"
            )
        published = peers.inbox.transactions.cntAll()
        assert not withdrawals(peers.inbox)
        firstNonce = max(r.nonce for _, r in peers.inbox.transactions.getItemIter()) + 1

        # FIFO burst: wallet 1, the unselected rival, then wallet 2.
        for label, wallet, sequence, issuer in (
            ("burst-1", 1, 10, None),
            ("burst-rival", 3, 900, rival),
            ("burst-2", 2, 10, None),
        ):
            assert (
                post(
                    peers,
                    event_body(
                        peers,
                        label,
                        sequence,
                        issuer=issuer,
                        wallet=WALLETS[wallet],
                        revoked=True,
                    ),
                ).status_code
                == 200
            )

        # Run until wallet 2's withdrawal is signed but unmined, then restart.
        for _ in range(40):
            yield from postingService.cycle()
            signed = pending_transaction(peers.inbox)
            if signed is not None and signed.associationSaid == said("burst-2"):
                break
        else:
            pytest.fail("second withdrawal was never signed")
        assert signed.functionName == "withdrawEndorsement"
        assert (yield from observe(effects, WALLETS[1]))[1][1] == ""
        assert (yield from observe(effects, WALLETS[2]))[1][1] == said("burst-2")
        peers.inbox.close()
        peers.inbox.reopen()
        # A new service over the reopened store, sharing the running RPC client.
        resumed = PostingService(
            reader=PostingReader(peers.inbox),
            associations=AssociationReader(peers.inbox),
            writer=PostingWriter(peers.inbox),
            effects=effects,
            account=postingService.account,
        )
        assert pending_transaction(peers.inbox) == signed
        yield from drain(resumed)

        records = withdrawals(peers.inbox)
        assert [r.associationSaid for r in records] == [
            said("burst-1"),
            said("burst-2"),
        ]
        assert [r.nonce for r in records] == [firstNonce, firstNonce + 1]
        assert records[1].txHash == signed.txHash
        assert all(r.state == "confirmed" for r in records)
        assert peers.inbox.transactions.cntAll() == published + 2
        assert len((yield from withdrawal_logs(effects))) == 2
        assert (yield from observe(effects, WALLETS[1]))[1][1] == ""
        assert (yield from observe(effects, WALLETS[2]))[1][1] == ""
        assert (yield from observe(effects, WALLETS[3]))[1][1] == said("burst-3")
        phase, unselected = PostingReader(peers.inbox).operation_for(
            f"{said('burst-rival')}:2"
        )
        assert phase == "completed" and unselected.outcome == "revoked"
        assert unselected.txHash == "" and unselected.lastTransactionHash == ""
        return True

    drive(postingService.effects, workflow)


def test_withdrawal_retried_after_it_landed_is_not_sent_twice(peers, deployment):
    """A lost receipt dead-letters a mined withdrawal; manual retry resolves it without resending."""
    postingService = posting_service_for(peers, deployment)
    wallet, association = WALLETS[1], said("landed")
    assert post(peers, event_body(peers, "landed", 10, wallet=wallet)).status_code == 200

    def workflow(effects):
        yield from drain(postingService)
        assert (yield from observe(effects, wallet))[1][1] == association
        revoked = event_body(peers, "landed", 10, wallet=wallet, revoked=True)
        assert post(peers, revoked).status_code == 200
        yield from postingService.cycle()  # Sign the withdrawal.
        yield from postingService.cycle()  # Broadcast it; automine lands it.
        signed = pending_transaction(peers.inbox)
        assert signed.functionName == "withdrawEndorsement"
        assert (yield from observe(effects, wallet))[1][1] == ""
        nonce = yield from effects.request(
            "eth_getTransactionCount", [effects.writer, "latest"]
        )

        def unavailable(tx_hash):
            yield 0.0
            raise TimeoutError("transaction receipt unavailable")

        with patch.object(effects, "transaction_receipt", unavailable):
            for _ in range(3):
                yield from postingService.cycle()
        phase, operation = PostingReader(peers.inbox).operation_for(
            f"{association}:2"
        )
        assert phase == "dead" and operation.txHash == signed.txHash
        peers.inbox.close()
        peers.inbox.reopen()
        RecoveryService(
            PostingReader(peers.inbox), PostingWriter(peers.inbox)
        ).retry_operation(operation.operationId)
        yield from drain(postingService)

        phase, operation = PostingReader(peers.inbox).operation_for(
            f"{association}:2"
        )
        assert phase == "completed"
        assert [r.txHash for r in withdrawals(peers.inbox)] == [signed.txHash]
        assert peers.inbox.transactions.get(keys=(signed.txHash,)).state == "confirmed"
        assert len((yield from withdrawal_logs(effects))) == 1
        assert (
            yield from effects.request(
                "eth_getTransactionCount", [effects.writer, "latest"]
            )
        ) == nonce
        assert (yield from observe(effects, wallet))[1][1] == ""
        # Exact redelivery of the revoked event creates no further effect.
        assert post(peers, revoked).status_code == 200
        with patch.object(effects, "request", side_effect=AssertionError("idle RPC")):
            yield from postingService.cycle()
        return True

    drive(postingService.effects, workflow)


def test_withdrawal_after_external_withdrawal_has_no_second_effect(peers, deployment):
    """The registry treats a repeated exact withdrawal as a no-op, so replays are safe."""
    postingService = posting_service_for(peers, deployment)
    wallet, association = WALLETS[1], said("external")
    assert post(peers, event_body(peers, "external", 10, wallet=wallet)).status_code == 200

    def workflow(effects):
        yield from drain(postingService)
        assert (yield from observe(effects, wallet))[1][1] == association
        # The withdrawal lands before the Oracle's own transaction does.
        yield from send(
            effects,
            effects.contracts["endorsement"],
            "withdrawEndorsement",
            [wallet, association],
            KEY,
        )
        assert len((yield from withdrawal_logs(effects))) == 1
        assert (
            post(
                peers, event_body(peers, "external", 10, wallet=wallet, revoked=True)
            ).status_code
            == 200
        )
        yield from drain(postingService)
        records = withdrawals(peers.inbox)
        assert len(records) == 1 and records[0].state == "confirmed"
        assert len((yield from withdrawal_logs(effects))) == 1
        assert (yield from observe(effects, wallet))[1][1] == ""
        phase, _ = PostingReader(peers.inbox).operation_for(f"{association}:2")
        assert phase == "completed"
        return True

    drive(postingService.effects, workflow)


def test_no_accepted_claim_is_promoted_after_withdrawal(peers, deployment):
    """A rejected claim stays rejected after the owner's withdrawal; only a new claim publishes."""
    postingService = posting_service_for(peers, deployment)
    rival = peers.receiver.hab.pre
    wallet = WALLETS[1]
    assert post(peers, event_body(peers, "owner", 10, wallet=wallet)).status_code == 200
    competitor = event_body(peers, "competitor", 900, issuer=rival, wallet=wallet)
    assert post(peers, competitor).status_code == 200

    def workflow(effects):
        yield from drain(postingService)
        assert (yield from observe(effects, wallet))[1][1] == said("owner")
        assert peers.inbox.statuses.get(keys=(said("competitor"),)).selection == "rejected"
        assert (
            post(
                peers, event_body(peers, "owner", 10, wallet=wallet, revoked=True)
            ).status_code
            == 200
        )
        yield from drain(postingService)
        assert (yield from observe(effects, wallet))[1][1] == ""
        transactions = peers.inbox.transactions.cntAll()
        competing = peers.inbox.statuses.get(keys=(said("competitor"),))
        assert (competing.status, competing.selection) == ("accepted", "rejected")
        assert AssociationReader(peers.inbox).selected("eip155:31337:" + wallet) is None

        # Redelivery cannot promote it, and nothing is sent.
        assert post(peers, competitor).status_code == 200
        with patch.object(effects, "request", side_effect=AssertionError("idle RPC")):
            for _ in range(3):
                yield from postingService.cycle()
        assert peers.inbox.transactions.cntAll() == transactions
        assert (yield from observe(effects, wallet))[1][1] == ""

        # A claim must be presented anew, as a new association.
        assert (
            post(
                peers, event_body(peers, "represented", 901, issuer=rival, wallet=wallet)
            ).status_code
            == 200
        )
        yield from settle(postingService, wallet, said("represented"))
        assert (
            AssociationReader(peers.inbox).selected("eip155:31337:" + wallet).associationSaid
            == said("represented")
        )
        return True

    drive(postingService.effects, workflow)

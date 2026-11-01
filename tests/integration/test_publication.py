"""Publication, replacement and exact association withdrawal against real contracts."""

from unittest.mock import patch

from tests.support.contracts import KEY, drive, observe, pending_transaction
from tests.support.posting import posting_service_for, settle
from tests.support.status_events import event_body, post, said
from vlei_oracle.persistence.associations import AssociationReader


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

"""receiving/test_status.py."""

import json

import pytest
from eoa_verifier.delivery.delivering import WebhookSender
from hio.base import doing

from tests.support.status_events import event_body, post, said, selected
from vlei_oracle.runtime.serving import StatusReceiver


def test_status_commit_survives_reordering_reopen_and_conflict(peers):
    """Revocation arriving first stays retired; duplicate ACKs never revive or rewrite it."""
    accepted = json.loads(peers.event.body)
    revoked = {
        **accepted,
        "eventId": f"{accepted['associationSaid']}:2",
        "statusRevision": 2,
        "status": "revoked",
        "reasonCode": "REVOKED",
    }
    body = json.dumps(revoked).encode()
    assert post(peers, body).status_code == 200
    peers.inbox.close()
    peers.inbox.reopen()
    assert post(peers, peers.event.body.encode()).status_code == 200
    assert post(peers, body).status_code == 200
    status = peers.inbox.statuses.get(keys=(accepted["associationSaid"],))
    assert (status.status, status.statusRevision) == ("revoked", 2)
    assert status.wallet == "0x" + "11" * 20
    assert len(list(peers.inbox.inbox.getItemIter())) == 2

    # Same revision with changed facts is a conflict, even with a valid signature.
    revoked["association"] = {**revoked["association"], "leSaid": said("other LE")}
    assert post(peers, json.dumps(revoked).encode()).status_code == 409
    assert peers.inbox.statuses.get(keys=(accepted["associationSaid"],)) == status


@pytest.mark.parametrize("gap", ["none", "before-ack", "before-sender-completion"])
def test_real_sender_recovers_commit_ack_gaps(peers, monkeypatch, gap):
    """Actual signed HTTP completes once, including automatic redelivery after either commit gap."""
    sender = WebhookSender(baser=peers.outbox, hab=peers.hab, timeout=1)

    def fail(*args, **kwargs):
        """Simulate process loss precisely at the selected durable/network boundary."""
        raise RuntimeError("simulated process loss")

    def drive(worker, *, expect_failure=False):
        """Run HIO HTTP and sender until one attempt settles, then close both sockets."""

        starting_attempts = peers.outbox.outbox.get(
            keys=(peers.event.eventId,)
        ).attempts

        def stop_when_settled(tymth, tock=0.0, **kwa):
            yield tock
            while True:
                event = peers.outbox.outbox.get(keys=(peers.event.eventId,))
                if event.acknowledged or (
                    event.attempts > starting_attempts
                    and event.error
                    and event.error != "attempt started; authenticated ACK not recorded"
                ):
                    parent.remove([worker, peers.receiver])
                    return
                yield 0.01

        parent = doing.DoDoer(
            doers=[peers.receiver, worker, doing.doify(stop_when_settled)]
        )
        scheduler = doing.Doist(real=True, tock=0.01, limit=3)
        if expect_failure:
            with pytest.raises(RuntimeError, match="simulated process loss"):
                scheduler.do(doers=[parent])
        else:
            scheduler.do(doers=[parent])
            assert parent.done, "sender did not settle before test deadline"

    with monkeypatch.context() as patch:
        if gap == "before-ack":
            from vlei_oracle.receiving.authing import ServiceAuthenticator

            patch.setattr(ServiceAuthenticator, "sign", fail)
        elif gap == "before-sender-completion":
            patch.setattr(peers.outbox, "delivery_result", fail)
        drive(sender, expect_failure=(gap == "before-sender-completion"))

    # The receiver has committed even when the ACK/completion boundary fails.
    assert peers.inbox.inbox.get(keys=(peers.event.eventId,)) is not None
    if gap != "none":
        assert not peers.outbox.outbox.get(keys=(peers.event.eventId,)).acknowledged
        # Reopen both durable stores and construct fresh workers, as after restart.
        peers.outbox.close()
        peers.outbox.reopen()
        peers.inbox.close()
        peers.inbox.reopen()
        peers.receiver = StatusReceiver(
            peers.config, hby=peers.oracle, baser=peers.inbox
        )
        sender = WebhookSender(baser=peers.outbox, hab=peers.hab, timeout=1)
        drive(sender)
    acknowledged = peers.outbox.outbox.get(keys=(peers.event.eventId,))
    assert acknowledged.acknowledged
    assert acknowledged.attempts == (1 if gap == "none" else 2)
    ext_id = json.loads(peers.event.body)["association"]["extId"]
    assert (
        peers.inbox.selected_association(ext_id).associationSaid
        == peers.event.associationSaid
    )
    assert len(list(peers.inbox.inbox.getItemIter())) == 1
    status = peers.inbox.statuses.get(keys=(peers.event.associationSaid,))
    assert (
        status.status == "accepted"
    )  # The old observation does not expire acceptance.
    from eth_utils import keccak

    assert status.identityRef == "0x" + keccak(text=peers.hab.pre).hex()
    assert not list(peers.inbox.transactions.getItemIter())
    assert not peers.receiver.server.servant.opened


def test_issued_then_revoked_is_terminal_and_chain_scoped(peers):
    """Normal retirement advances status; a new acceptance cannot undo it or change chain."""
    accepted = json.loads(peers.event.body)
    assert post(peers, peers.event.body.encode()).status_code == 200
    revoked = {
        **accepted,
        "eventId": f"{accepted['associationSaid']}:2",
        "statusRevision": 2,
        "status": "revoked",
        "reasonCode": "REVOKED",
    }
    assert post(peers, json.dumps(revoked).encode()).status_code == 200
    revival = {
        **accepted,
        "eventId": f"{accepted['associationSaid']}:3",
        "statusRevision": 3,
    }
    assert post(peers, json.dumps(revival).encode()).status_code == 409
    revival["association"] = {
        **accepted["association"],
        "extId": "eip155:1:0x" + "11" * 20,
    }
    assert post(peers, json.dumps(revival).encode()).status_code == 400
    assert (
        peers.inbox.statuses.get(keys=(accepted["associationSaid"],)).status
        == "revoked"
    )


def test_missing_facts_cannot_displace_owner(peers):
    """Incomplete facts never supply authority to replace the selected association."""
    assert post(peers, peers.event.body.encode()).status_code == 200
    incomplete = json.loads(event_body(peers, "missing", 20))
    del incomplete["association"]["issuanceAnchor"]
    assert post(peers, json.dumps(incomplete).encode()).status_code == 400
    assert selected(peers).associationSaid == peers.event.associationSaid

"""receiving/test_status.py."""

import json

from tests.support.status_events import event_body, post, said, selected


def test_status_commit_survives_reordering_reopen_and_conflict(peers):
    """Revocation arriving first stays revoked; duplicate ACKs never revive or rewrite it."""
    acceptedEvent = json.loads(peers.event.body)
    revokedEvent = {
        **acceptedEvent,
        "eventId": f"{acceptedEvent['associationSaid']}:2",
        "statusRevision": 2,
        "status": "revoked",
        "reasonCode": "REVOKED",
    }
    body = json.dumps(revokedEvent).encode()
    assert post(peers, body).status_code == 200
    peers.inbox.close()
    peers.inbox.reopen()
    assert post(peers, peers.event.body.encode()).status_code == 200
    assert post(peers, body).status_code == 200
    status = peers.inbox.statuses.get(keys=(acceptedEvent["associationSaid"],))
    assert (status.status, status.statusRevision) == ("revoked", 2)
    assert status.wallet == "0x" + "11" * 20
    assert len(list(peers.inbox.inbox.getItemIter())) == 2

    # Same revision with changed facts is a conflict, even with a valid signature.
    revokedEvent["association"] = {
        **revokedEvent["association"],
        "leSaid": said("other LE"),
    }
    assert post(peers, json.dumps(revokedEvent).encode()).status_code == 409
    assert peers.inbox.statuses.get(keys=(acceptedEvent["associationSaid"],)) == status


def test_issued_then_revoked_is_terminal_and_chain_scoped(peers):
    """Normal revocation advances status; a new acceptance cannot undo it or change chain."""
    acceptedEvent = json.loads(peers.event.body)
    assert post(peers, peers.event.body.encode()).status_code == 200
    revokedEvent = {
        **acceptedEvent,
        "eventId": f"{acceptedEvent['associationSaid']}:2",
        "statusRevision": 2,
        "status": "revoked",
        "reasonCode": "REVOKED",
    }
    assert post(peers, json.dumps(revokedEvent).encode()).status_code == 200
    revivalEvent = {
        **acceptedEvent,
        "eventId": f"{acceptedEvent['associationSaid']}:3",
        "statusRevision": 3,
    }
    assert post(peers, json.dumps(revivalEvent).encode()).status_code == 409
    revivalEvent["association"] = {
        **acceptedEvent["association"],
        "extId": "eip155:1:0x" + "11" * 20,
    }
    assert post(peers, json.dumps(revivalEvent).encode()).status_code == 400
    assert (
        peers.inbox.statuses.get(keys=(acceptedEvent["associationSaid"],)).status
        == "revoked"
    )


def test_missing_facts_cannot_displace_owner(peers):
    """Incomplete facts never supply authority to replace the selected association."""
    assert post(peers, peers.event.body.encode()).status_code == 200
    incomplete = json.loads(event_body(peers, "missing", 20))
    del incomplete["association"]["issuanceAnchor"]
    assert post(peers, json.dumps(incomplete).encode()).status_code == 400
    assert selected(peers).associationSaid == peers.event.associationSaid
    status = peers.inbox.statuses.get(keys=(peers.event.associationSaid,))
    assert (
        status.status == "accepted"
    )  # Old observation metadata never expires acceptance.
    from eth_utils import keccak

    assert status.identityRef == "0x" + keccak(text=peers.hab.pre).hex()
    assert not list(peers.inbox.transactions.getItemIter())


def test_selection_and_serialization_finish_before_write_transaction(peers):
    """Status-event policy and serialization run outside even a short LMDB writer."""
    from contextlib import contextmanager
    from unittest.mock import patch

    from vlei_oracle.receiving import status_events

    env = peers.inbox.env
    active = False
    selected_outside_write = False
    serialized_outside_write = False
    select = status_events.update_selection
    serialize = peers.inbox.statuses.serializer

    class ObservedEnvironment:
        """Observe real transaction lifetimes without replacing LMDB operations."""

        def __getattr__(self, name):
            return getattr(env, name)

        @contextmanager
        def begin(self, *args, **kwargs):
            nonlocal active
            with env.begin(*args, **kwargs) as txn:
                active = kwargs.get("write", False)
                try:
                    yield txn
                finally:
                    active = False

    def select_without_writer(*args, **kwargs):
        nonlocal selected_outside_write
        assert not active
        selected_outside_write = True
        return select(*args, **kwargs)

    def serialize_without_writer(value):
        nonlocal serialized_outside_write
        assert not active
        serialized_outside_write = True
        return serialize(value)

    with (
        patch.object(peers.inbox, "env", ObservedEnvironment()),
        patch.object(status_events, "update_selection", select_without_writer),
        patch.object(peers.inbox.statuses, "serializer", serialize_without_writer),
    ):
        assert post(peers, event_body(peers, "outside-writer", 10)).status_code == 200
    assert selected_outside_write and serialized_outside_write

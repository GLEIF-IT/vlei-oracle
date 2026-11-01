"""receiving/test_status.py."""

import json


from tests.support.status_events import event_body, post, said, selected


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
    status = peers.inbox.statuses.get(keys=(peers.event.associationSaid,))
    assert (
        status.status == "accepted"
    )  # Old observation metadata never expires acceptance.
    from eth_utils import keccak

    assert status.identityRef == "0x" + keccak(text=peers.hab.pre).hex()
    assert not list(peers.inbox.transactions.getItemIter())

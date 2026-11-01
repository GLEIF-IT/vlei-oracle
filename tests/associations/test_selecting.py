"""associations/test_selecting.py."""

import pytest

from tests.support.status_events import event_body, post, said, selected


@pytest.mark.parametrize("order", [("older", "newer"), ("newer", "older")])
def test_same_le_selects_later_issuance_and_never_restores_predecessor(peers, order):
    """Issuance order wins over arrival; delayed old revocation and replay cannot undo replacement."""
    bodies = {
        label: event_body(peers, label, sn)
        for label, sn in (("older", 10), ("newer", 12))
    }
    for label in order:
        assert post(peers, bodies[label]).status_code == 200
    assert selected(peers).associationSaid == said("newer")
    assert peers.inbox.statuses.get(keys=(said("older"),)).selection == "superseded"
    peers.inbox.close()
    peers.inbox.reopen()
    assert selected(peers).associationSaid == said("newer")

    assert post(peers, event_body(peers, "older", 10, revoked=True)).status_code == 200
    assert selected(peers).associationSaid == said("newer")
    assert post(peers, event_body(peers, "newer", 12, revoked=True)).status_code == 200
    assert selected(peers) is None
    assert post(peers, bodies["older"]).status_code == 200
    # Even an undiscovered older issuance cannot refill the released claim.
    assert post(peers, event_body(peers, "delayed", 11)).status_code == 200
    assert selected(peers) is None
    assert peers.inbox.statuses.get(keys=(said("delayed"),)).selection == "superseded"


def test_other_le_requires_release_and_a_new_claim(peers):
    """Reject a competing LE regardless of its KEL sequence; transfer only after current revocation."""
    owner = event_body(peers, "owner", 10)
    other = (
        peers.receiver.hab.pre
    )  # A distinct KERI AID used only as a fixture LE label.
    competitor = event_body(peers, "competitor", 900, issuer=other)
    assert post(peers, owner).status_code == 200
    assert (
        post(peers, competitor).status_code == 200
    )  # ACK is receipt, not ownership approval.
    assert selected(peers).associationSaid == said("owner")
    assert peers.inbox.statuses.get(keys=(said("competitor"),)).selection == "rejected"
    assert post(peers, event_body(peers, "owner", 10, revoked=True)).status_code == 200
    assert post(peers, competitor).status_code == 200
    assert (
        selected(peers) is None
    )  # A previously rejected receipt is not promoted by replay.

    assert (
        post(peers, event_body(peers, "transfer", 901, issuer=other)).status_code == 200
    )
    assert selected(peers).associationSaid == said("transfer")
    # A rejected/old SAID's revocation cannot release the transferred claim.
    assert (
        post(
            peers, event_body(peers, "competitor", 900, issuer=other, revoked=True)
        ).status_code
        == 200
    )
    assert selected(peers).associationSaid == said("transfer")
    assert (
        post(
            peers, event_body(peers, "transfer", 901, issuer=other, revoked=True)
        ).status_code
        == 200
    )
    assert post(peers, event_body(peers, "owner-late", 9)).status_code == 200
    assert selected(peers) is None  # Retain each issuer's history across a transfer.
    assert post(peers, event_body(peers, "owner-return", 11)).status_code == 200
    assert selected(peers).associationSaid == said(
        "owner-return"
    )  # Never compare 11 against 901.


def test_newer_revocation_before_acceptance_preserves_supersession(peers):
    """A newer issuance supersedes its predecessor even when revoked status arrives first."""
    assert post(peers, event_body(peers, "older", 10)).status_code == 200
    assert post(peers, event_body(peers, "newer", 12, revoked=True)).status_code == 200
    assert selected(peers) is None
    assert peers.inbox.statuses.get(keys=(said("older"),)).selection == "superseded"
    peers.inbox.close()
    peers.inbox.reopen()
    assert post(peers, event_body(peers, "newer", 12)).status_code == 200
    assert selected(peers) is None

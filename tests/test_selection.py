"""Ownership policy through authenticated receipt, with native peer fixtures and durable stores."""

import json
from unittest.mock import patch

import pytest

from vlei_oracle.receiving.service import StatusReceiver
from test_status_inbox import peers, post, said


def event_body(peers, label, sequence, *, issuer=None, revoked=False):
    """Make post-verification fixture facts; this helper does not assert credential validity."""
    body = json.loads(peers.event.body)
    body["associationSaid"] = said(label)
    body["revision"] = 2 if revoked else 1
    body["eventId"] = f"{body['associationSaid']}:{body['revision']}"
    body["association"] = {
        **body["association"],
        "issuerAid": issuer or peers.hab.pre,
        "leSaid": said(
            f"LE-{label}"
        ),  # LE credential SAID is not the holder's identity.
        "issuance": {"sequenceNumber": sequence, "said": said(f"anchor-{label}")},
    }
    if revoked:
        body.update(outcome="invalid", reasonCode="REVOKED")
    return json.dumps(body).encode()


def selected(peers):
    """Read the selected association for the fixture's canonical chain/address."""
    return peers.inbox.selected_association(
        json.loads(peers.event.body)["association"]["extId"]
    )


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
    )  # A distinct native AID used only as a fixture LE label.
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


def test_newer_retirement_before_acceptance_preserves_supersession(peers):
    """A newer same-LE retirement still prevents its older predecessor remaining selected."""
    assert post(peers, event_body(peers, "older", 10)).status_code == 200
    assert post(peers, event_body(peers, "newer", 12, revoked=True)).status_code == 200
    assert selected(peers) is None
    assert peers.inbox.statuses.get(keys=(said("older"),)).selection == "superseded"
    peers.inbox.close()
    peers.inbox.reopen()
    assert post(peers, event_body(peers, "newer", 12)).status_code == 200
    assert selected(peers) is None


def test_receipt_and_selection_rollback_together(peers):
    """A failed claim write cannot ACK or partially supersede the prior selected association."""
    assert post(peers, event_body(peers, "older", 10)).status_code == 200
    body = event_body(peers, "newer", 12)
    with patch.object(
        peers.inbox.claims, "serializer", side_effect=RuntimeError("write failed")
    ):
        assert post(peers, body).status_code == 500
    assert selected(peers).associationSaid == said("older")
    assert selected(peers).selection == "selected"
    assert peers.inbox.statuses.get(keys=(said("newer"),)) is None
    assert peers.inbox.inbox.get(keys=(f"{said('newer')}:1",)) is None
    assert post(peers, body).status_code == 200
    assert selected(peers).associationSaid == said("newer")


def test_missing_facts_cannot_displace_owner(peers):
    """Incomplete facts never supply authority to replace the selected association."""
    assert post(peers, peers.event.body.encode()).status_code == 200
    incomplete = json.loads(event_body(peers, "missing", 20))
    del incomplete["association"]["issuance"]
    assert post(peers, json.dumps(incomplete).encode()).status_code == 400
    assert selected(peers).associationSaid == peers.event.associationSaid


def test_old_inbox_requires_fresh_selection_store(peers):
    """Do not invent ownership history or erase receipts when opening a populated PR05 inbox."""
    assert post(peers, peers.event.body.encode()).status_code == 200
    # Represent the PR05 schema: receipts/status, but no ownership-decision history.
    peers.inbox.claims.rem(keys=(json.loads(peers.event.body)["association"]["extId"],))
    with pytest.raises(ValueError, match="fresh receiver store"):
        StatusReceiver(peers.config, hby=peers.oracle, baser=peers.inbox)
    assert peers.inbox.inbox.get(keys=(peers.event.eventId,)) is not None

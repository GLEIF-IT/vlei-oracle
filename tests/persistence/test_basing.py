"""persistence/test_basing.py."""

from unittest.mock import patch

import pytest
from eth_account import Account

from tests.support.contracts import persist_intent, signed_intent
from tests.support.status_events import event_body, post, said, selected
from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.persistence.records import PostingScope

WALLET = Account.from_key("0x" + "0" * 63 + "1").address

IDENTITY = "0x" + "12" * 32


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


@pytest.mark.parametrize("field", ["chainId", "writer", "endorsement", "identity"])
def test_operation_store_refuses_different_deployment_scope(tmp_path, field):
    store = OracleBaser(name="scope", headDirPath=str(tmp_path))
    scope = PostingScope(31337, WALLET.lower(), WALLET.lower(), WALLET.lower())
    store.bind_posting_scope(scope)
    store.close()
    reopened = OracleBaser(name="scope", headDirPath=str(tmp_path))
    try:
        reopened.bind_posting_scope(scope)
        with pytest.raises(ValueError, match="posting scope"):
            from dataclasses import replace

            reopened.bind_posting_scope(
                replace(
                    scope, **{field: 31338 if field == "chainId" else "0x" + "ab" * 20}
                )
            )
    finally:
        reopened.close(clear=True)


def test_signed_attempts_are_immutable_across_reopen(tmp_path):
    from dataclasses import replace

    store = OracleBaser(name="immutable", headDirPath=str(tmp_path))
    transaction = {
        "nonce": 0,
        "gasPrice": 1,
        "gas": 21000,
        "to": WALLET,
        "value": 0,
        "chainId": 31337,
    }
    intent = persist_intent(store, signed_intent(transaction))
    store.close()
    store.reopen()
    try:
        with pytest.raises(ValueError, match="signed attempt"):
            persist_intent(store, replace(intent, associationSaid="different"))
        with pytest.raises(ValueError, match="signed attempt"):
            persist_intent(store, replace(intent, rawTransaction="0x00"))
    finally:
        store.close(clear=True)


def test_receipt_and_phase_transition_are_atomic(peers):
    """Queue creation cannot be lost at ACK; a failed phase move retains its source."""
    body = event_body(peers, "atomic-work", 99)
    with patch.object(
        peers.inbox.queued, "serializer", side_effect=RuntimeError("queue failed")
    ):
        assert post(peers, body).status_code == 500
    assert peers.inbox.statuses.get(keys=(said("atomic-work"),)) is None
    assert post(peers, body).status_code == 200
    assert post(peers, body).status_code == 200
    assert peers.inbox.queued.cntAll() == 1
    work = peers.inbox.begin_operation()
    with patch.object(
        peers.inbox.completed, "serializer", side_effect=RuntimeError("move failed")
    ):
        with pytest.raises(RuntimeError, match="move failed"):
            peers.inbox.move_operation(work, "preparing", "completed")
    peers.inbox.close()
    peers.inbox.reopen()
    assert peers.inbox.preparing.get(keys=("writer",)) == work
    assert peers.inbox.completed.cntAll() == 0

"""persistence/test_basing.py."""

from unittest.mock import patch

import pytest
from eth_account import Account

from tests.support.contracts import persist_transaction, signed_transaction
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
    txRecord = persist_transaction(store, signed_transaction(transaction))
    store.close()
    store.reopen()
    try:
        with pytest.raises(ValueError, match="signed attempt"):
            persist_transaction(store, replace(txRecord, associationSaid="different"))
        with pytest.raises(ValueError, match="signed attempt"):
            persist_transaction(store, replace(txRecord, rawTransaction="0x00"))
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
    postingOperation = peers.inbox.begin_operation()
    with patch.object(
        peers.inbox.completed, "serializer", side_effect=RuntimeError("move failed")
    ):
        with pytest.raises(RuntimeError, match="move failed"):
            peers.inbox.move_operation(postingOperation, "preparing", "completed")
    peers.inbox.close()
    peers.inbox.reopen()
    assert peers.inbox.preparing.get(keys=("writer",)) == postingOperation
    assert peers.inbox.completed.cntAll() == 0


def test_confirmation_rollback_retains_pending_intent_and_previous_effects(tmp_path):
    """A failed phase write cannot record a receipt or applied effect independently."""
    from dataclasses import replace

    from keri.db import dbing

    from vlei_oracle.persistence.records import WalletEffects

    with dbing.openLMDB(cls=OracleBaser, name="confirm-rollback", temp=True) as store:
        txRecord = persist_transaction(
            store,
            signed_transaction(
                {
                    "nonce": 0,
                    "gasPrice": 1,
                    "gas": 21000,
                    "to": WALLET,
                    "value": 0,
                    "chainId": 31337,
                }
            ),
        )
        original = store.pending.get(keys=("writer",))
        resolved = replace(original, txHash="")
        with (
            patch.object(
                store.preparing, "serializer", side_effect=RuntimeError("phase failed")
            ),
            pytest.raises(RuntimeError, match="phase failed"),
        ):
            store.move_operation(
                resolved,
                "pending",
                "preparing",
                txRecord=replace(txRecord, state="confirmed"),
                walletEffects=WalletEffects(identityRef=IDENTITY),
            )
        assert store.pending.get(keys=("writer",)) == original
        assert store.transactions.get(keys=(txRecord.txHash,)) == txRecord
        assert store.walletEffects.get(keys=(original.wallet,)) is None

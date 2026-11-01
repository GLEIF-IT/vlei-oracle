"""Atomic writer integrity and rollback against real LMDB tables."""

from unittest.mock import patch

import pytest
from eth_account import Account

from tests.support.contracts import persist_transaction, signed_transaction
from tests.support.status_events import event_body, post, said, selected
from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.persistence.posting import PostingReader, PostingWriter

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
    postingOperation = PostingReader(peers.inbox).next_queued()
    assert peers.inbox.queued.cntAll() == 1
    assert PostingReader(peers.inbox).next_preparing() is None
    assert PostingReader(peers.inbox).next_pending() is None
    PostingWriter(peers.inbox).queued_to_preparing(postingOperation)
    with patch.object(
        peers.inbox.completed, "serializer", side_effect=RuntimeError("move failed")
    ):
        with pytest.raises(RuntimeError, match="move failed"):
            PostingWriter(peers.inbox).preparing_to_completed(postingOperation)
    peers.inbox.close()
    peers.inbox.reopen()
    assert peers.inbox.preparing.get(keys=("writer",)) == postingOperation
    assert peers.inbox.completed.cntAll() == 0


def test_confirmation_rollback_retains_pending_transaction_and_previous_effects(
    tmp_path,
):
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
        writer = PostingWriter(store)
        put_transaction = writer._put_transaction

        def fail_after_journal_write(txn, record, raw):
            """Interrupt after the actual LMDB journal mutation, before phase completion."""
            put_transaction(txn, record, raw)
            raise RuntimeError("phase failed")

        with (
            patch.object(
                writer, "_put_transaction", side_effect=fail_after_journal_write
            ),
            pytest.raises(RuntimeError, match="phase failed"),
        ):
            writer.pending_to_preparing(
                resolved,
                txRecord=replace(txRecord, state="confirmed"),
                walletEffects=WalletEffects(identityRef=IDENTITY),
            )
        assert store.pending.get(keys=("writer",)) == original
        assert store.transactions.get(keys=(txRecord.txHash,)) == txRecord
        assert store.walletEffects.get(keys=(original.wallet,)) is None


def test_receipt_write_failure_rolls_back_supersession(peers):
    """Abort after real status writes; receipt and queue must not partially commit."""
    from contextlib import contextmanager

    assert post(peers, event_body(peers, "prior", 10)).status_code == 200
    env = peers.inbox.env

    class FailingTransaction:
        """Let LMDB mutate status rows, then interrupt the subsequent claim write."""

        def __init__(self, txn):
            self.txn = txn

        def __getattr__(self, name):
            return getattr(self.txn, name)

        def put(self, key, value, **kwargs):
            if kwargs.get("db") == peers.inbox.claims.sdb:
                raise RuntimeError("claim write interrupted")
            return self.txn.put(key, value, **kwargs)

    class FailingEnvironment:
        @contextmanager
        def begin(self, *args, **kwargs):
            with env.begin(*args, **kwargs) as txn:
                yield FailingTransaction(txn) if kwargs.get("write") else txn

    with patch.object(peers.inbox, "env", FailingEnvironment()):
        assert post(peers, event_body(peers, "successor", 12)).status_code == 500
    assert selected(peers).associationSaid == said("prior")
    assert selected(peers).selection == "selected"
    assert peers.inbox.statuses.get(keys=(said("successor"),)) is None
    assert peers.inbox.inbox.get(keys=(f"{said('successor')}:1",)) is None
    assert peers.inbox.queued.cntAll() == 1
    assert peers.inbox.sequence.get(keys=("writer",)).next == 1

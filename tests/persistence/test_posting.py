"""Immutable signed transactions and atomic posting outcomes."""

from unittest.mock import patch

import pytest
from eth_account import Account

from tests.support.contracts import persist_transaction, signed_transaction
from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.persistence.posting import PostingWriter

WALLET = Account.from_key("0x" + "0" * 63 + "1").address

IDENTITY = "0x" + "12" * 32


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


def test_confirmation_rollback_retains_pending_transaction_and_previous_effects(
    tmp_path,
):
    """A failed phase write cannot persist a transaction outcome or applied effect independently."""
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
        put_transaction = writer._put_tx

        def fail_after_journal_write(txn, record, raw):
            """Interrupt after the actual LMDB journal mutation, before phase completion."""
            put_transaction(txn, record, raw)
            raise RuntimeError("phase failed")

        with (
            patch.object(writer, "_put_tx", side_effect=fail_after_journal_write),
            pytest.raises(RuntimeError, match="phase failed"),
        ):
            writer.pending_to_prep(
                resolved,
                txRecord=replace(txRecord, state="confirmed"),
                walletEffects=WalletEffects(identityRef=IDENTITY),
            )
        assert store.pending.get(keys=("writer",)) == original
        assert store.transactions.get(keys=(txRecord.txHash,)) == txRecord
        assert store.walletEffects.get(keys=(original.wallet,)) is None

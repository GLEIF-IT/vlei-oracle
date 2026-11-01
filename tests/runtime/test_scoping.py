"""Advisory configuration changes preserve durable work across restart."""

from dataclasses import replace

import pytest
from keri.db import dbing

from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.persistence.records import (
    InboxScope,
    PostingOperation,
    PostingScope,
    TransactionRecord,
    WalletEffects,
)
from vlei_oracle.persistence.scope import ScopeStore
from vlei_oracle.runtime.scoping import note_inbox, note_posting

WALLET = "0x" + "11" * 20


@pytest.mark.parametrize("field", ["chainId", "writer", "endorsement", "identity"])
def test_posting_change_warns_and_preserves_journal(tmp_path, caplog, field):
    """Changed settings are advisory even when signed work and effects remain."""
    options = dict(cls=OracleBaser, name="scope", headDirPath=str(tmp_path), temp=False)
    scope = PostingScope(31337, WALLET, WALLET, WALLET)
    changed = replace(
        scope, **{field: 31338 if field == "chainId" else "0x" + "ab" * 20}
    )
    operation = PostingOperation("op", "event", "credential", WALLET, txHash="hash")
    tx = TransactionRecord(
        "hash", "signed-bytes", 7, "credential", "publishEndorsement"
    )
    effects = WalletEffects(associationSaid="previous")
    with dbing.openLMDB(**options) as store:
        note_posting(ScopeStore(store), scope)
        store.pending.pin(keys=("writer",), val=operation)
        store.transactions.pin(keys=(tx.txHash,), val=tx)
        store.walletEffects.pin(keys=(WALLET,), val=effects)
    assert not caplog.records
    with dbing.openLMDB(**options) as store:
        note_posting(ScopeStore(store), changed)
        assert store.pending.get(keys=("writer",)) == operation
        assert store.transactions.get(keys=(tx.txHash,)) == tx
        assert store.walletEffects.get(keys=(WALLET,)) == effects
    assert "Oracle posting settings changed" in caplog.text
    assert str(getattr(scope, field)) in caplog.text
    assert str(getattr(changed, field)) in caplog.text
    assert caplog.records[-1].levelname == "WARNING"
    caplog.clear()
    with dbing.openLMDB(**options) as store:
        note_posting(ScopeStore(store), changed)
        assert not caplog.records
        note_posting(ScopeStore(store), scope)
    assert "Oracle posting settings changed" in caplog.text


@pytest.mark.parametrize(
    "field,value", [("chainId", 31338), ("verifierAid", "new-verifier")]
)
def test_inbox_change_updates_warning_baseline(tmp_path, caplog, field, value):
    """First use and unchanged reopen are quiet; each actual change warns once."""
    options = dict(cls=OracleBaser, name="inbox", headDirPath=str(tmp_path), temp=False)
    scope = InboxScope("verifier", 31337)
    changed = replace(scope, **{field: value})
    with dbing.openLMDB(**options) as store:
        note_inbox(ScopeStore(store), scope)
    assert not caplog.records
    with dbing.openLMDB(**options) as store:
        note_inbox(ScopeStore(store), changed)
    assert "Oracle inbox settings changed" in caplog.text
    assert str(getattr(scope, field)) in caplog.text
    assert str(value) in caplog.text
    caplog.clear()
    with dbing.openLMDB(**options) as store:
        note_inbox(ScopeStore(store), changed)
        assert not caplog.records
        note_inbox(ScopeStore(store), scope)
    assert "Oracle inbox settings changed" in caplog.text

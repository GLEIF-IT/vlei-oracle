"""LMDB lifecycle and typed table bindings for Oracle application records."""

from __future__ import annotations

from typing import Any

from keri.db import dbing, koming

from vlei_oracle.associations.records import AssociationStatus, StatusEvent, WalletClaim

from .records import (
    InboxScope,
    PostingOperation,
    PostingScope,
    PostingSequence,
    ReplayClaim,
    TransactionRecord,
    WalletEffects,
)


class OracleBaser(dbing.LMDBer):
    """Own the environment and table handles; application services own decisions."""

    TailDirPath = "vlei-oracle/db"
    AltTailDirPath = ".vlei-oracle/db"
    TempPrefix = "vlei_oracle_db_"

    def __init__(self, name: str = "vlei-oracle", reopen: bool = True, **kwa: Any):
        super().__init__(name=name, reopen=reopen, **kwa)

    def reopen(self, **kwa: Any):
        """Open LMDB and bind the independently owned record families."""

        super().reopen(**kwa)
        self.transactions = koming.Komer(
            db=self, subkey="txns.", schema=TransactionRecord
        )
        self.scope = koming.Komer(db=self, subkey="scope.", schema=PostingScope)
        self.inbox = koming.Komer(db=self, subkey="inbox.", schema=StatusEvent)
        self.statuses = koming.Komer(
            db=self, subkey="statuses.", schema=AssociationStatus
        )
        self.replay_claims = koming.Komer(
            db=self, subkey="replay_claims.", schema=ReplayClaim
        )
        self.claims = koming.Komer(db=self, subkey="claims.", schema=WalletClaim)
        self.inbox_scope = koming.Komer(
            db=self, subkey="inbox-scope.", schema=InboxScope
        )
        self.queued = koming.Komer(db=self, subkey="queued.", schema=PostingOperation)
        self.preparing = koming.Komer(
            db=self, subkey="preparing.", schema=PostingOperation
        )
        self.pending = koming.Komer(db=self, subkey="pending.", schema=PostingOperation)
        self.completed = koming.Komer(
            db=self, subkey="completed.", schema=PostingOperation
        )
        self.dead = koming.Komer(db=self, subkey="dead.", schema=PostingOperation)
        self.walletEffects = koming.Komer(
            db=self, subkey="wallet-effects.", schema=WalletEffects
        )
        self.sequence = koming.Komer(
            db=self, subkey="sequence.", schema=PostingSequence
        )
        return self.env

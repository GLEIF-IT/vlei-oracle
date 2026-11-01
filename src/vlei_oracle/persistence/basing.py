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
    TransactionRecord,
    WalletEffects,
)


class OracleBaser(dbing.LMDBer):
    """Own the application LMDB environment and non-duplicate JSON record bindings.

    Use name/base/headDirPath/temp/reopen through the KERIpy LMDBer lifecycle.
    Persistent close retains inbox history, posting work and signed transactions;
    temporary context closure follows LMDBer's cleanup. Services decide selection,
    posting steps and recovery outside this layer. No table refreshes chain state.
    """

    # Persistent application-store directory below LMDBer's head/base/name roots.
    TailDirPath = "vlei-oracle/db"
    # LMDBer alternate relative directory when the default root is unavailable.
    AltTailDirPath = ".vlei-oracle/db"
    # Prefix for LMDBer-owned disposable test-store directories.
    TempPrefix = "vlei_oracle_db_"

    def __init__(self, name: str = "vlei-oracle", reopen: bool = True, **kwa: Any):
        """Configure the named application store and optionally open it immediately.

        name defaults to vlei-oracle; reopen=False defers opening/binding until reopen().
        kwa forwards standard LMDBer options such as base, headDirPath and temp. The
        owning runtime/context closes the environment; consumers borrow its handles.
        """
        super().__init__(name=name, reopen=reopen, **kwa)

    def reopen(self, **kwa: Any):
        """Open LMDB, bind each local subdatabase and return the environment.

        kwa passes to LMDBer.reopen. All bindings below are non-duplicate Komers with
        UTF-8 identifier keys and JSON dataclass values. Ordered operation IDs are
        zero-padded 16-hex arrival numbers; fixed receiver/posting/writer keys identify
        single slots. Binding the schema performs no application workflow or network/RPC effects.
        """

        super().reopen(**kwa)
        # Transaction hash -> TransactionRecord: exact signed bytes saved before broadcast.
        # Retain the recorded receipt outcome; never replace previously signed bytes.
        self.transactions = koming.Komer(
            db=self, subkey="txns.", schema=TransactionRecord
        )
        # "posting" -> PostingScope: last deployment/writer settings, for warnings only.
        self.scope = koming.Komer(db=self, subkey="scope.", schema=PostingScope)
        # Verifier event ID -> StatusEvent: exact authenticated JSON and operation ID.
        # Retained for duplicate/conflict detection; old events may have no operation.
        self.inbox = koming.Komer(db=self, subkey="inbox.", schema=StatusEvent)
        # Credential SAID -> AssociationStatus: latest verifier facts and local selection.
        self.statuses = koming.Komer(
            db=self, subkey="statuses.", schema=AssociationStatus
        )
        # Canonical eip155:<chain>:<address> -> WalletClaim: selected credential SAID
        # and per-issuer issuance floors, retained after release to reject predecessors.
        self.claims = koming.Komer(db=self, subkey="claims.", schema=WalletClaim)
        # "receiver" -> InboxScope: last authorized producer/chain, for warnings only.
        self.inbox_scope = koming.Komer(
            db=self, subkey="inbox-scope.", schema=InboxScope
        )
        # Ordered operation ID -> PostingOperation: arrival-ordered unsigned posting work.
        self.queued = koming.Komer(db=self, subkey="queued.", schema=PostingOperation)
        # "writer" -> PostingOperation: sole current/interrupted preparation step.
        self.preparing = koming.Komer(
            db=self, subkey="preparing.", schema=PostingOperation
        )
        # "writer" -> PostingOperation: sole signed transaction awaiting an RPC outcome.
        self.pending = koming.Komer(db=self, subkey="pending.", schema=PostingOperation)
        # Operation ID -> PostingOperation: terminal publication/withdrawal outcome.
        self.completed = koming.Komer(
            db=self, subkey="completed.", schema=PostingOperation
        )
        # Operation ID -> PostingOperation: bounded failure awaiting operator recovery.
        # An unresolved signed transaction also fences the writer through sequence.
        self.dead = koming.Komer(db=self, subkey="dead.", schema=PostingOperation)
        # Wallet address -> WalletEffects: only this writer's confirmed contract effects.
        # Used to select later steps; never refreshed by scanning on-chain mappings.
        self.walletEffects = koming.Komer(
            db=self, subkey="wallet-effects.", schema=WalletEffects
        )
        # "writer" -> PostingSequence: next arrival number and unresolved dead-letter ID.
        self.sequence = koming.Komer(
            db=self, subkey="sequence.", schema=PostingSequence
        )
        return self.env

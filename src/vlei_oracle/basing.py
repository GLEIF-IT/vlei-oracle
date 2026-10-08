"""LMDB-backed state for long-running vLEI Oracle presentation operations.

Operations are keyed by association SAID and survive process restarts.
The store records association progress, verified results, and transactions. Pending association
operations also serve as the durable work list for HIO verification. A lost
in-memory request or result cue is recovered by repeating read-only checks from
the accepted credential; it does not require a second persistent queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from keri.db import dbing, koming
from keri.help import helping

STATE_AWAITING_ARTIFACTS = "awaiting_artifacts"
STATE_VERIFYING = "verifying"
STATE_VERIFIED = "verified"
STATE_FAILED = "failed"
STATE_ENDORSED = "endorsed"
STATE_WITHDRAWN = "withdrawn"


@dataclass
class PostingScope:
    """Immutable chain/deployment/writer scope of this operation store."""

    chainId: int
    writer: str
    endorsement: str
    identity: str


@dataclass
class TransactionRecord:
    """Durable intent and signed identity, written before any broadcast."""

    txHash: str
    rawTransaction: str
    transaction: dict[str, Any]
    associationSaid: str
    action: str
    state: str = "pending"
    receiptHash: str = ""
    receiptNumber: int = 0
    cancelHash: str = ""
    cancelRaw: str = ""
    failureReason: str = ""


@dataclass
class OperationRecord:
    """Progress for one association, keyed by its immutable credential SAID."""

    associationSaid: str
    state: str = STATE_VERIFYING
    grantSaid: str = ""
    grantSender: str = ""
    associationSchema: str = ""
    createdAt: str = ""
    updatedAt: str = ""
    failureReason: str = ""
    retryCount: int = 0
    verified: dict[str, Any] = field(default_factory=dict)
    txHashes: list[str] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        association_said: str,
        grant_said: str,
        grant_sender: str,
        association_schema: str,
    ) -> OperationRecord:
        """Create fresh verification work from an accepted Grant."""

        now = helping.nowIso8601()
        return cls(
            associationSaid=association_said,
            state=STATE_VERIFYING,
            grantSaid=grant_said,
            grantSender=grant_sender,
            associationSchema=association_schema,
            createdAt=now,
            updatedAt=now,
        )

    def touch(self) -> None:
        """Refresh the durable update timestamp before persisting."""

        self.updatedAt = helping.nowIso8601()

    def to_json(self) -> dict[str, Any]:
        """Return the public status shape exposed by the REST endpoint."""

        return {
            "associationSaid": self.associationSaid,
            "state": self.state,
            "grantSaid": self.grantSaid,
            "grantSender": self.grantSender,
            "associationSchema": self.associationSchema,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            "failureReason": self.failureReason,
            "retryCount": self.retryCount,
            "verified": self.verified,
            "txHashes": self.txHashes,
        }


class OracleBaser(dbing.LMDBer):
    """Local durable operation store for the always-on oracle service."""

    TailDirPath = "vlei-oracle/db"
    AltTailDirPath = ".vlei-oracle/db"
    TempPrefix = "vlei_oracle_db_"

    def __init__(self, name: str = "vlei-oracle", reopen: bool = True, **kwa: Any):
        self.ops: koming.Komer | None = None
        super().__init__(name=name, reopen=reopen, **kwa)

    def reopen(self, **kwa: Any):
        """Open the LMDB environment and bind the operation Komer."""

        super().reopen(**kwa)
        self.ops = koming.Komer(db=self, subkey="ops.", schema=OperationRecord)
        self.transactions = koming.Komer(db=self, subkey="txns.", schema=TransactionRecord)
        self.scope = koming.Komer(db=self, subkey="scope.", schema=PostingScope)
        return self.env

    def bind_posting_scope(self, scope: PostingScope):
        """Refuse to reconcile an old intent against a different deployment."""
        existing = self.scope.get(keys=("posting",))
        if existing is not None and existing != scope:
            raise ValueError("operation store belongs to a different posting scope")
        self.scope.pin(keys=("posting",), val=scope)

    def pin_transaction(self, intent: TransactionRecord):
        """Commit intent, cancellation, or receipt progress synchronously."""
        self.transactions.pin(keys=(intent.txHash,), val=intent)
        return intent

    def unfinished_transaction(self):
        """The single writer reconciles its outstanding nonce before new work."""
        for _, intent in self.transactions.getItemIter():
            if intent.state != "confirmed":
                return intent
        return None

    def get_operation(self, association_said: str) -> OperationRecord | None:
        """Return the operation keyed by association SAID, if present."""

        return self.ops.get(keys=(association_said,))

    def pin_operation(self, op: OperationRecord) -> OperationRecord:
        """Persist an operation after refreshing its update timestamp."""

        op.touch()
        self.ops.pin(keys=(op.associationSaid,), val=op)
        return op

    def start_operation(
        self,
        *,
        association_said: str,
        grant_said: str,
        grant_sender: str,
        association_schema: str,
    ) -> OperationRecord:
        """Create an operation from an IPEX Grant, preserving idempotency."""

        existing = self.get_operation(association_said)
        if existing is not None:
            return existing
        op = OperationRecord.new(
            association_said=association_said,
            grant_said=grant_said,
            grant_sender=grant_sender,
            association_schema=association_schema,
        )
        self.ops.pin(keys=(association_said,), val=op)
        return op

    def mark_awaiting_artifacts(
        self, op: OperationRecord, reason: str
    ) -> OperationRecord:
        """Record that source-chain artifacts are still arriving."""

        if op.state == STATE_AWAITING_ARTIFACTS and op.failureReason == reason:
            return op
        op.state = STATE_AWAITING_ARTIFACTS
        op.failureReason = reason
        return self.pin_operation(op)

    def mark_failed(self, op: OperationRecord, reason: str) -> OperationRecord:
        """Mark an operation terminal after verification or posting failure."""

        op.state = STATE_FAILED
        op.failureReason = reason
        return self.pin_operation(op)

    def operations_by_state(self, *states: str):
        """Yield operations currently in any requested state."""

        wanted = set(states)
        for _, op in self.ops.getItemIter():
            if op.state in wanted:
                yield op

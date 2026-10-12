"""Native LMDB ownership for verification, managed associations, and effects.

Verification owns acceptance progress; management owns continuing reconciliation.
Their handoff commits atomically. Queues and cues remain transient scheduling
hints, while the transaction journal owns recovery of uncertain external effects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from keri.db import dbing, koming

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
class VerificationWork:
    """Progress for one association, keyed by its immutable credential SAID."""

    associationSaid: str
    state: str = STATE_VERIFYING
    grantSaid: str = ""
    failureReason: str = ""

    def to_json(self) -> dict[str, Any]:
        """Return only local work status; evidence is assessed separately."""
        return asdict(self)


@dataclass
class ManagedAssociation:
    """Explicit membership retained after withdrawal to preserve supersession."""

    associationSaid: str
    state: str = STATE_VERIFIED
    failureReason: str = ""
    txHashes: list[str] = field(default_factory=list)


class OracleBaser(dbing.LMDBer):
    """Local durable operation store for the always-on oracle service."""

    TailDirPath = "vlei-oracle/db"
    AltTailDirPath = ".vlei-oracle/db"
    TempPrefix = "vlei_oracle_db_"

    def __init__(self, name: str = "vlei-oracle", reopen: bool = True, **kwa: Any):
        self.verifications: koming.Komer | None = None
        super().__init__(name=name, reopen=reopen, **kwa)

    def reopen(self, **kwa: Any):
        """Open the LMDB environment and bind the operation Komer."""

        super().reopen(**kwa)
        self.verifications = koming.Komer(db=self, subkey="verification.", schema=VerificationWork)
        self.managed = koming.Komer(db=self, subkey="managed.", schema=ManagedAssociation)
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

    def get_verification(self, association_said: str) -> VerificationWork | None:
        """Return the operation keyed by association SAID, if present."""

        return self.verifications.get(keys=(association_said,))

    def pin_verification(self, op: VerificationWork) -> VerificationWork:
        """Persist only current verification progress and intervention reason."""

        self.verifications.pin(keys=(op.associationSaid,), val=op)
        return op

    def start_verification(
        self,
        *,
        association_said: str,
        grant_said: str,
    ) -> VerificationWork | None:
        """Create an operation from an IPEX Grant, preserving idempotency."""

        if self.get_managed(association_said) is not None:
            return None
        existing = self.get_verification(association_said)
        if existing is not None:
            return existing
        op = VerificationWork(associationSaid=association_said, grantSaid=grant_said)
        self.verifications.pin(keys=(association_said,), val=op)
        return op

    def get_managed(self, association_said: str) -> ManagedAssociation | None:
        return self.managed.get(keys=(association_said,))

    def pin_managed(self, association: ManagedAssociation) -> ManagedAssociation:
        self.managed.pin(keys=(association.associationSaid,), val=association)
        return association

    def complete_verification(self, work: VerificationWork) -> None:
        """Atomically finish verification and enroll exactly once.

        Only the verifier calls this after its post-RPC acceptance recheck.
        LMDB commit leaves pending work or managed membership, never a gap.
        Management owns its subsequent state, even for duplicate result cues.
        """
        key = work.associationSaid.encode()
        with self.env.begin(write=True) as txn:
            current = self.verifications.deserializer(txn.get(key, db=self.verifications.sdb))
            if txn.get(key, db=self.managed.sdb):
                return
            if current is None or current.state != STATE_VERIFYING:
                raise ValueError("only unfinished verification can enroll an association")
            txn.delete(key, db=self.verifications.sdb)
            member = ManagedAssociation(associationSaid=work.associationSaid)
            txn.put(key, self.managed.serializer(member), db=self.managed.sdb, overwrite=False)

    def presentation_status(self, association_said: str) -> dict | None:
        """Compose local lifecycle status without RPC or evidence reconstruction."""
        member = self.get_managed(association_said)
        if member is not None:
            return {**asdict(member), "verificationState": STATE_VERIFIED, "managed": True}
        work = self.get_verification(association_said)
        if work is None:
            return None
        return {**work.to_json(), "verificationState": work.state,
                "managed": False, "txHashes": []}

    def mark_awaiting_artifacts(
        self, op: VerificationWork, reason: str
    ) -> VerificationWork:
        """Record that source-chain artifacts are still arriving."""

        if op.state == STATE_AWAITING_ARTIFACTS and op.failureReason == reason:
            return op
        op.state = STATE_AWAITING_ARTIFACTS
        op.failureReason = reason
        return self.pin_verification(op)

    def mark_failed(self, op: VerificationWork, reason: str) -> VerificationWork:
        """Mark an operation terminal after verification or posting failure."""

        op.state = STATE_FAILED
        op.failureReason = reason
        return self.pin_verification(op)

    def verification_by_state(self, *states: str):
        """Yield operations currently in any requested state."""

        wanted = set(states)
        for _, op in self.verifications.getItemIter():
            if op.state in wanted:
                yield op

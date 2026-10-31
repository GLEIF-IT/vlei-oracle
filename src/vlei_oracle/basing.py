"""LMDB records for the legacy workflow and trusted status receiver.

Verification owns acceptance progress; management owns continuing reconciliation.
Their handoff commits atomically. Queues and cues remain transient scheduling
hints, while the transaction journal owns recovery of uncertain external effects.
The receive-only service instead commits authenticated status and ownership
selection together; it does not use the legacy verification/effect workflow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from keri.db import dbing, koming

from vlei_oracle.statusing import AssociationStatus, StatusEvent
from vlei_oracle.selecting import WalletClaim, update_selection

STATE_AWAITING_ARTIFACTS = "awaiting_artifacts"
STATE_VERIFYING = "verifying"
STATE_VERIFIED = "verified"
STATE_FAILED = "failed"
STATE_ENDORSED = "endorsed"
STATE_WITHDRAWN = "withdrawn"


@dataclass
class PostingScope:
    """Immutable chain/deployment/writer scope of the recovery journal."""

    chainId: int
    writer: str
    endorsement: str
    identity: str


@dataclass
class TransactionRecord:
    """Durable intent and signed identity, written before any broadcast."""

    txHash: str
    rawTransaction: str
    nonce: int
    gasPrice: int
    associationSaid: str
    action: str
    state: str = "pending"
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


@dataclass
class ManagedAssociation:
    """Explicit membership retained after withdrawal to preserve supersession."""

    associationSaid: str
    state: str = STATE_VERIFIED
    failureReason: str = ""


@dataclass
class ReplayClaim:
    """Authenticated transport nonce lifetime, independent of event deduplication."""

    expiresAt: int


@dataclass
class InboxScope:
    """One trusted producer and chain for the durable status history."""

    verifierAid: str
    chainId: int


class OracleBaser(dbing.LMDBer):
    """Own legacy workflow records and the independent trusted receipt/selection records."""

    TailDirPath = "vlei-oracle/db"
    AltTailDirPath = ".vlei-oracle/db"
    TempPrefix = "vlei_oracle_db_"

    def __init__(self, name: str = "vlei-oracle", reopen: bool = True, **kwa: Any):
        self.verifications: koming.Komer | None = None
        super().__init__(name=name, reopen=reopen, **kwa)

    def reopen(self, **kwa: Any):
        """Open LMDB and bind the independently owned record families."""

        super().reopen(**kwa)
        self.verifications = koming.Komer(db=self, subkey="verification.", schema=VerificationWork)
        self.managed = koming.Komer(db=self, subkey="managed.", schema=ManagedAssociation)
        self.transactions = koming.Komer(db=self, subkey="txns.", schema=TransactionRecord)
        self.scope = koming.Komer(db=self, subkey="scope.", schema=PostingScope)
        self.inbox = koming.Komer(db=self, subkey="inbox.", schema=StatusEvent)
        self.statuses = koming.Komer(db=self, subkey="statuses.", schema=AssociationStatus)
        self.replay_claims = koming.Komer(db=self, subkey="replay_claims.", schema=ReplayClaim)
        self.claims = koming.Komer(db=self, subkey="claims.", schema=WalletClaim)
        self.inbox_scope = koming.Komer(db=self, subkey="inbox-scope.", schema=InboxScope)
        return self.env

    def bind_inbox_scope(self, scope):
        """Refuse a changed trust scope or a populated inbox with no selection history."""
        existing = self.inbox_scope.get(keys=("receiver",))
        if existing is not None and existing != scope:
            raise ValueError("inbox belongs to another verifier or chain")
        if self.statuses.cntAll() and not self.claims.cntAll():
            raise ValueError("inbox predates ownership selection; use a fresh receiver store")
        self.inbox_scope.put(keys=("receiver",), val=scope)

    def claim_nonce(self, peer, nonce, expires_at):
        """Claim an authenticated attempt once; persistent claims reject replay."""
        return self.replay_claims.put(keys=(peer, nonce), val=ReplayClaim(expires_at))

    def receive_status(self, event, status):
        """Commit event receipt, monotonic status and ownership selection before ACK.

        Exact redelivery and older revisions are acknowledged without changing
        current status. Conflicting bytes/revisions or issuance facts are rejected.
        Revocation is terminal for this SAID; another SAID may later replace it.
        Selection uses trusted issuance history only; no contract effect occurs.
        """
        event_key, status_key = event.eventId.encode(), status.associationSaid.encode()
        with self.env.begin(write=True) as txn:
            prior = self.inbox.deserializer(txn.get(event_key, db=self.inbox.sdb))
            if prior is not None:
                if prior != event:
                    raise ValueError("conflicting event revision")
                return
            current = self.statuses.deserializer(txn.get(status_key, db=self.statuses.sdb))
            if current is not None:
                if current.association != status.association:
                    raise ValueError("association facts cannot change")
                if current.status == "revoked" and status.status != "revoked" and status.statusRevision > current.statusRevision:
                    raise ValueError("revoked association cannot be reaccepted")
                if current.status == "accepted" and status.status == "revoked" and status.statusRevision < current.statusRevision:
                    raise ValueError("revocation revision precedes acceptance")
            txn.put(event_key, self.inbox.serializer(event), db=self.inbox.sdb)
            if current is None or status.statusRevision > current.statusRevision:
                if current is not None:
                    status.selection = current.selection
                self._apply_status(txn, status)

    def _apply_status(self, txn, status):
        """Apply selection and persist all changed records inside the receipt transaction."""
        key = status.association["extId"].encode()
        claim = self.claims.deserializer(txn.get(key, db=self.claims.sdb)) or WalletClaim()
        current = (
            self.statuses.deserializer(txn.get(claim.currentSaid.encode(), db=self.statuses.sdb))
            if claim.currentSaid else None
        )
        update_selection(status, current=current, claim=claim)
        if current is not None and current.associationSaid != status.associationSaid:
            txn.put(
                current.associationSaid.encode(), self.statuses.serializer(current), db=self.statuses.sdb
            )
        txn.put(status.associationSaid.encode(), self.statuses.serializer(status), db=self.statuses.sdb)
        txn.put(key, self.claims.serializer(claim), db=self.claims.sdb)

    def selected_association(self, ext_id):
        """Read the current selected status for a canonical chain/address, without RPC."""
        with self.env.begin() as txn:
            claim = self.claims.deserializer(txn.get(ext_id.encode(), db=self.claims.sdb))
            if claim is None or not claim.currentSaid:
                return None
            return self.statuses.deserializer(txn.get(claim.currentSaid.encode(), db=self.statuses.sdb))

    def bind_posting_scope(self, scope: PostingScope):
        """Refuse to reconcile an old intent against a different deployment."""
        existing = self.scope.get(keys=("posting",))
        if existing is not None and existing != scope:
            raise ValueError("workflow store belongs to a different posting scope")
        self.scope.pin(keys=("posting",), val=scope)

    def pin_transaction(self, intent: TransactionRecord):
        """Keep signed attempts immutable while recording chain progress.

        Original and same-nonce cancellation bytes must be durable before their
        first broadcast. Observations may change; an attempt identity may not.
        """
        existing = self.transactions.get(keys=(intent.txHash,))
        if existing is not None:
            for field in ("rawTransaction", "nonce", "gasPrice", "associationSaid", "action"):
                if getattr(existing, field) != getattr(intent, field):
                    raise ValueError("cannot change a persisted signed attempt")
            if existing.cancelHash and (existing.cancelHash, existing.cancelRaw) != (intent.cancelHash, intent.cancelRaw):
                raise ValueError("cannot change a persisted cancellation")
        if bool(intent.cancelHash) != bool(intent.cancelRaw):
            raise ValueError("cancellation requires both hash and signed bytes")
        self.transactions.pin(keys=(intent.txHash,), val=intent)
        return intent

    def complete_transaction(self, intent, tx_hash, succeeded):
        """Save the attempt outcome, not its reconstructible receipt snapshot."""
        if tx_hash not in (intent.txHash, intent.cancelHash):
            raise ValueError("completion belongs to another signed attempt")
        state = (
            "cancelled" if tx_hash == intent.cancelHash and succeeded
            else "cancellation_reverted" if tx_hash == intent.cancelHash
            else "confirmed" if succeeded else "reverted"
        )
        if intent.state != state or intent.failureReason:
            intent.state, intent.failureReason = state, ""
            self.pin_transaction(intent)

    def unfinished_transaction(self):
        """The single writer reconciles its outstanding nonce before new work."""
        for _, intent in self.transactions.getItemIter():
            if intent.state == "pending":
                return intent
        return None

    def transaction_status(self, association_said):
        """Read relationships directly from the recovery journal, without RPC."""
        return [{"txHash": intent.txHash, "cancelHash": intent.cancelHash,
                 "action": intent.action, "state": intent.state,
                 "failureReason": intent.failureReason}
                for _, intent in self.transactions.getItemIter()
                if intent.associationSaid == association_said]

    def get_verification(self, association_said: str) -> VerificationWork | None:
        """Return verification work keyed by association SAID, if present."""

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
        """Start Grant verification once; managed associations stay enrolled."""

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

    def association_status(self, association_said: str) -> dict | None:
        """Compose local lifecycle status without RPC or evidence reconstruction."""
        member = self.get_managed(association_said)
        if member is not None:
            return {**asdict(member), "verificationState": STATE_VERIFIED,
                    "managed": True, "transactions": self.transaction_status(association_said)}
        work = self.get_verification(association_said)
        if work is None:
            return None
        return {**asdict(work), "verificationState": work.state,
                "managed": False, "transactions": self.transaction_status(association_said)}

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
        """Record an established verification rejection for operator intervention."""

        op.state = STATE_FAILED
        op.failureReason = reason
        return self.pin_verification(op)

    def verification_by_state(self, *states: str):
        """Yield verification work in any requested state."""

        wanted = set(states)
        for _, op in self.verifications.getItemIter():
            if op.state in wanted:
                yield op

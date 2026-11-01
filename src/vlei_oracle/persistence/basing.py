"""Atomic LMDB receipt, wallet selection and transaction journal."""

from __future__ import annotations

from typing import Any

from keri.db import dbing, koming

from vlei_oracle.associations.records import AssociationStatus, StatusEvent, WalletClaim
from vlei_oracle.associations.selecting import update_selection

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
    """Own trusted receipt, ownership selection and single-writer journal records."""

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

    def bind_inbox_scope(self, scope):
        """Bind receipt and selection to one configured verifier and chain."""
        existing = self.inbox_scope.get(keys=("receiver",))
        if existing is not None and existing != scope:
            raise ValueError("inbox belongs to another verifier or chain")
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
                if prior.body != event.body:
                    raise ValueError("conflicting event revision")
                return
            current = self.statuses.deserializer(
                txn.get(status_key, db=self.statuses.sdb)
            )
            if current is not None:
                if current.association != status.association:
                    raise ValueError("association facts cannot change")
                if (
                    current.status == "revoked"
                    and status.status != "revoked"
                    and status.statusRevision > current.statusRevision
                ):
                    raise ValueError("revoked association cannot be reaccepted")
                if (
                    current.status == "accepted"
                    and status.status == "revoked"
                    and status.statusRevision < current.statusRevision
                ):
                    raise ValueError("revocation revision precedes acceptance")
            if current is None or status.statusRevision > current.statusRevision:
                if current is not None:
                    status.selection = current.selection
                self._apply_status(txn, status)
                sequence = (
                    self.sequence.deserializer(txn.get(b"writer", db=self.sequence.sdb))
                    or PostingSequence()
                )
                sequence.next += 1
                event.operationId = f"{sequence.next:016x}"
                postingOperation = PostingOperation(
                    event.operationId,
                    event.eventId,
                    status.associationSaid,
                    status.wallet,
                )
                txn.put(
                    event.operationId.encode(),
                    self.queued.serializer(postingOperation),
                    db=self.queued.sdb,
                )
                txn.put(
                    b"writer", self.sequence.serializer(sequence), db=self.sequence.sdb
                )
            txn.put(event_key, self.inbox.serializer(event), db=self.inbox.sdb)

    def _apply_status(self, txn, status):
        """Apply selection and persist all changed records inside the receipt transaction."""
        key = status.association["extId"].encode()
        claim = (
            self.claims.deserializer(txn.get(key, db=self.claims.sdb)) or WalletClaim()
        )
        current = (
            self.statuses.deserializer(
                txn.get(claim.currentSaid.encode(), db=self.statuses.sdb)
            )
            if claim.currentSaid
            else None
        )
        update_selection(status, current=current, claim=claim)
        if current is not None and current.associationSaid != status.associationSaid:
            txn.put(
                current.associationSaid.encode(),
                self.statuses.serializer(current),
                db=self.statuses.sdb,
            )
        txn.put(
            status.associationSaid.encode(),
            self.statuses.serializer(status),
            db=self.statuses.sdb,
        )
        txn.put(key, self.claims.serializer(claim), db=self.claims.sdb)

    def selected_association(self, ext_id):
        """Read the current selected status for a canonical chain/address, without RPC."""
        with self.env.begin() as txn:
            claim = self.claims.deserializer(
                txn.get(ext_id.encode(), db=self.claims.sdb)
            )
            if claim is None or not claim.currentSaid:
                return None
            return self.statuses.deserializer(
                txn.get(claim.currentSaid.encode(), db=self.statuses.sdb)
            )

    def bind_posting_scope(self, scope: PostingScope):
        """Refuse to use stored transactions with a different deployment."""
        existing = self.scope.get(keys=("posting",))
        if existing is not None and existing != scope:
            raise ValueError("store belongs to a different posting scope")
        self.scope.pin(keys=("posting",), val=scope)

    def begin_operation(self):
        """Move the oldest queued operation into the sole writer slot atomically."""
        with self.env.begin(write=True) as txn:
            sequence = (
                self.sequence.deserializer(txn.get(b"writer", db=self.sequence.sdb))
                or PostingSequence()
            )
            if sequence.blockedBy or txn.get(b"writer", db=self.pending.sdb):
                return None
            activeOperation = self.preparing.deserializer(
                txn.get(b"writer", db=self.preparing.sdb)
            )
            if activeOperation:
                return activeOperation
            with txn.cursor(db=self.queued.sdb) as cursor:
                if not cursor.first():
                    return None
                key, raw = cursor.item()
                postingOperation = self.queued.deserializer(raw)
                txn.put(
                    b"writer",
                    self.preparing.serializer(postingOperation),
                    db=self.preparing.sdb,
                )
                txn.delete(key, db=self.queued.sdb)
                return postingOperation

    def move_operation(
        self,
        postingOperation,
        source,
        destination,
        *,
        walletEffects=None,
        txRecord=None,
    ):
        """Commit one phase transition with its journal/effect changes or nothing.

        Active phases use one writer slot; terminal phases use the operation ID.
        The signed transaction is durable before any network submission. A dead signed
        transaction fences all later work until explicitly resumed and resolved.
        """
        source_key = (
            postingOperation.operationId.encode() if source == "dead" else b"writer"
        )
        target_key = (
            b"writer"
            if destination in ("preparing", "pending")
            else postingOperation.operationId.encode()
        )
        origin, target = getattr(self, source), getattr(self, destination)
        with self.env.begin(write=True) as txn:
            currentOperation = origin.deserializer(txn.get(source_key, db=origin.sdb))
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected phase")
            if txRecord is not None:
                existingTxRecord = self.transactions.deserializer(
                    txn.get(txRecord.txHash.encode(), db=self.transactions.sdb)
                )
                if existingTxRecord is not None and any(
                    getattr(existingTxRecord, field) != getattr(txRecord, field)
                    for field in (
                        "rawTransaction",
                        "nonce",
                        "associationSaid",
                        "functionName",
                    )
                ):
                    raise ValueError("cannot change a persisted signed attempt")
                txn.put(
                    txRecord.txHash.encode(),
                    self.transactions.serializer(txRecord),
                    db=self.transactions.sdb,
                )
            if walletEffects is not None:
                txn.put(
                    postingOperation.wallet.encode(),
                    self.walletEffects.serializer(walletEffects),
                    db=self.walletEffects.sdb,
                )
            txn.delete(source_key, db=origin.sdb)
            txn.put(target_key, target.serializer(postingOperation), db=target.sdb)
            sequence = (
                self.sequence.deserializer(txn.get(b"writer", db=self.sequence.sdb))
                or PostingSequence()
            )
            if destination == "dead" and postingOperation.txHash:
                sequence.blockedBy = postingOperation.operationId
            elif (
                source == "dead" and sequence.blockedBy == postingOperation.operationId
            ):
                sequence.blockedBy = ""
            txn.put(b"writer", self.sequence.serializer(sequence), db=self.sequence.sdb)

    def retry_operation(self, operation_id):
        """Explicit local operator retry; signed dead letters retain their exact transaction."""
        if self.preparing.get(keys=("writer",)) or self.pending.get(keys=("writer",)):
            raise ValueError("writer already has active work")
        sequence = self.sequence.get(keys=("writer",)) or PostingSequence()
        if sequence.blockedBy and sequence.blockedBy != operation_id:
            raise ValueError("another unresolved signed operation blocks the writer")
        postingOperation = self.dead.get(keys=(operation_id,))
        if postingOperation is None:
            raise ValueError("unknown dead-letter operation")
        postingOperation.attempts = postingOperation.rpcFailures = 0
        postingOperation.error = ""
        # The caller restarts the pending deadline on its first resumed cycle.
        postingOperation.signedAt = 0.0
        postingOperation.submitted = False
        self.move_operation(
            postingOperation,
            "dead",
            "pending" if postingOperation.txHash else "preparing",
        )

    def operation_for(self, event_id):
        """Return a detached phase/record for one event, without history iteration."""
        statusEvent = self.inbox.get(keys=(event_id,))
        if statusEvent is None or not statusEvent.operationId:
            return None, None
        for phase in ("queued", "preparing", "pending", "completed", "dead"):
            key = (
                "writer"
                if phase in ("preparing", "pending")
                else statusEvent.operationId
            )
            postingOperation = getattr(self, phase).get(keys=(key,))
            if (
                postingOperation is not None
                and postingOperation.operationId == statusEvent.operationId
            ):
                return phase, postingOperation
        raise ValueError("stored event has no delivery phase")

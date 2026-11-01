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

    def next_pending(self):
        """Read the sole pending signed operation without changing its phase."""
        return self.pending.get(keys=("writer",))

    def next_preparing(self):
        """Read interrupted preparation without consulting queued operations."""
        return self.preparing.get(keys=("writer",))

    def next_queued(self):
        """Detach the oldest queued operation and close its reader before returning."""
        with self.env.begin(db=self.queued.sdb) as txn, txn.cursor() as cursor:
            return (
                self.queued.deserializer(bytes(cursor.value()))
                if cursor.first()
                else None
            )

    def queued_to_preparing(self, postingOperation):
        """Claim the selected queued operation in the sole preparation slot."""
        key = postingOperation.operationId.encode()
        expected = self.queued.serializer(postingOperation)
        raw = self.preparing.serializer(postingOperation)
        with self.env.begin(write=True) as txn:
            if txn.get(key, db=self.queued.sdb) != expected:
                raise ValueError("queued operation is absent or changed")
            if txn.get(b"writer", db=self.preparing.sdb) or txn.get(
                b"writer", db=self.pending.sdb
            ):
                raise ValueError("writer already has active work")
            txn.delete(key, db=self.queued.sdb)
            txn.put(b"writer", raw, db=self.preparing.sdb)

    def preparing_to_pending(self, postingOperation, txRecord):
        """Journal the signed transaction and move preparing work to pending atomically."""
        source_key, target_key = b"writer", b"writer"
        raw = self.pending.serializer(postingOperation)
        tx_raw = self.transactions.serializer(txRecord)
        with self.env.begin(write=True) as txn:
            currentOperation = self.preparing.deserializer(
                txn.get(source_key, db=self.preparing.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected preparing phase")
            self._put_transaction(txn, txRecord, tx_raw)
            txn.delete(source_key, db=self.preparing.sdb)
            txn.put(target_key, raw, db=self.pending.sdb)

    def preparing_to_completed(self, postingOperation):
        """Remove preparing work and retain its terminal publication outcome."""
        source_key, target_key = b"writer", postingOperation.operationId.encode()
        raw = self.completed.serializer(postingOperation)
        with self.env.begin(write=True) as txn:
            currentOperation = self.preparing.deserializer(
                txn.get(source_key, db=self.preparing.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected preparing phase")
            txn.delete(source_key, db=self.preparing.sdb)
            txn.put(target_key, raw, db=self.completed.sdb)

    def preparing_to_dead(self, postingOperation):
        """Stop unsigned preparation without blocking later queued operations."""
        source_key, target_key = b"writer", postingOperation.operationId.encode()
        raw = self.dead.serializer(postingOperation)
        with self.env.begin(write=True) as txn:
            currentOperation = self.preparing.deserializer(
                txn.get(source_key, db=self.preparing.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected preparing phase")
            txn.delete(source_key, db=self.preparing.sdb)
            txn.put(target_key, raw, db=self.dead.sdb)

    def pending_to_preparing(self, postingOperation, txRecord, walletEffects=None):
        """Record a resolved receipt and optional confirmed effects before the next step."""
        source_key, target_key = b"writer", b"writer"
        raw = self.preparing.serializer(postingOperation)
        tx_raw = self.transactions.serializer(txRecord)
        effects_raw = (
            self.walletEffects.serializer(walletEffects)
            if walletEffects is not None
            else None
        )
        with self.env.begin(write=True) as txn:
            currentOperation = self.pending.deserializer(
                txn.get(source_key, db=self.pending.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected pending phase")
            self._put_transaction(txn, txRecord, tx_raw)
            if effects_raw is not None:
                txn.put(
                    postingOperation.wallet.encode(),
                    effects_raw,
                    db=self.walletEffects.sdb,
                )
            txn.delete(source_key, db=self.pending.sdb)
            txn.put(target_key, raw, db=self.preparing.sdb)

    def pending_to_dead(self, postingOperation, sequence, txRecord=None):
        """Stop pending work with its prepared writer fence and optional resolved receipt."""
        source_key, target_key = b"writer", postingOperation.operationId.encode()
        raw = self.dead.serializer(postingOperation)
        tx_raw = (
            self.transactions.serializer(txRecord) if txRecord is not None else None
        )
        sequence_raw = self.sequence.serializer(sequence)
        with self.env.begin(write=True) as txn:
            currentOperation = self.pending.deserializer(
                txn.get(source_key, db=self.pending.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected pending phase")
            if txRecord is not None:
                self._put_transaction(txn, txRecord, tx_raw)
            txn.delete(source_key, db=self.pending.sdb)
            txn.put(target_key, raw, db=self.dead.sdb)
            txn.put(b"writer", sequence_raw, db=self.sequence.sdb)

    def dead_to_pending(self, postingOperation, sequence):
        """Resume the same signed transaction and clear its prepared writer fence."""
        source_key, target_key = postingOperation.operationId.encode(), b"writer"
        raw = self.pending.serializer(postingOperation)
        sequence_raw = self.sequence.serializer(sequence)
        with self.env.begin(write=True) as txn:
            currentOperation = self.dead.deserializer(
                txn.get(source_key, db=self.dead.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected dead phase")
            if txn.get(b"writer", db=self.preparing.sdb) or txn.get(
                b"writer", db=self.pending.sdb
            ):
                raise ValueError("writer already has active work")
            txn.delete(source_key, db=self.dead.sdb)
            txn.put(target_key, raw, db=self.pending.sdb)
            txn.put(b"writer", sequence_raw, db=self.sequence.sdb)

    def dead_to_preparing(self, postingOperation, sequence):
        """Resume unsigned preparation with the operator-reset attempt budget."""
        source_key, target_key = postingOperation.operationId.encode(), b"writer"
        raw = self.preparing.serializer(postingOperation)
        sequence_raw = self.sequence.serializer(sequence)
        with self.env.begin(write=True) as txn:
            currentOperation = self.dead.deserializer(
                txn.get(source_key, db=self.dead.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected dead phase")
            if txn.get(b"writer", db=self.preparing.sdb) or txn.get(
                b"writer", db=self.pending.sdb
            ):
                raise ValueError("writer already has active work")
            txn.delete(source_key, db=self.dead.sdb)
            txn.put(target_key, raw, db=self.preparing.sdb)
            txn.put(b"writer", sequence_raw, db=self.sequence.sdb)

    def _put_transaction(self, txn, txRecord, raw):
        """Save journal progress while refusing mutation of previously signed bytes."""
        key = txRecord.txHash.encode()
        existing = self.transactions.deserializer(
            txn.get(key, db=self.transactions.sdb)
        )
        if existing is not None and any(
            getattr(existing, field) != getattr(txRecord, field)
            for field in ("rawTransaction", "nonce", "associationSaid", "functionName")
        ):
            raise ValueError("cannot change a persisted signed attempt")
        txn.put(key, raw, db=self.transactions.sdb)

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

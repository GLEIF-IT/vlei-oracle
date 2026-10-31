"""Atomic LMDB receipt, wallet selection and transaction journal."""

from __future__ import annotations

from typing import Any

from keri.db import dbing, koming

from vlei_oracle.associations.records import AssociationStatus, StatusEvent, WalletClaim
from vlei_oracle.associations.selecting import update_selection

from .records import InboxScope, PostingScope, ReplayClaim, TransactionRecord


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
                if prior != event:
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
            txn.put(event_key, self.inbox.serializer(event), db=self.inbox.sdb)
            if current is None or status.statusRevision > current.statusRevision:
                if current is not None:
                    status.selection = current.selection
                self._apply_status(txn, status)

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
        """Refuse to reconcile an old intent against a different deployment."""
        existing = self.scope.get(keys=("posting",))
        if existing is not None and existing != scope:
            raise ValueError("store belongs to a different posting scope")
        self.scope.pin(keys=("posting",), val=scope)

    def pin_transaction(self, intent: TransactionRecord):
        """Keep signed attempts immutable while recording chain progress."""
        existing = self.transactions.get(keys=(intent.txHash,))
        if existing is not None:
            for field in ("rawTransaction", "nonce", "associationSaid", "action"):
                if getattr(existing, field) != getattr(intent, field):
                    raise ValueError("cannot change a persisted signed attempt")
        self.transactions.pin(keys=(intent.txHash,), val=intent)
        return intent

    def complete_transaction(self, intent, tx_hash, succeeded):
        """Save the attempt outcome, not its reconstructible receipt snapshot."""
        if tx_hash != intent.txHash:
            raise ValueError("completion belongs to another signed attempt")
        state = "confirmed" if succeeded else "reverted"
        if intent.state != state or intent.failureReason:
            intent.state, intent.failureReason = state, ""
            self.pin_transaction(intent)

    def unfinished_transaction(self):
        """The single writer reconciles its outstanding nonce before new work."""
        for _, intent in self.transactions.getItemIter():
            if intent.state == "pending":
                return intent
        return None

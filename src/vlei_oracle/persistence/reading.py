"""Bounded reads returning detached application records, never live cursors."""


class OracleReader:
    """Borrow an open OracleBaser; every call finishes its LMDB read before returning."""

    def __init__(self, baser):
        self.baser = baser

    def event(self, eventId):
        """Return the exact previously received event, or None."""
        return self.baser.inbox.get(keys=(eventId,))

    def association(self, credSaid):
        """Return one association's latest status, or None."""
        return self.baser.statuses.get(keys=(credSaid,))

    def claim(self, extId):
        """Return selection history for one canonical chain/address, or None."""
        return self.baser.claims.get(keys=(extId,))

    def posting_sequence(self):
        """Return arrival order and the unresolved-signature fence, or None."""
        return self.baser.sequence.get(keys=("writer",))

    def selected_association(self, ext_id):
        """Read the current selected status for a canonical chain/address, without RPC."""
        with self.baser.env.begin() as txn:
            claim = self.baser.claims.deserializer(
                txn.get(ext_id.encode(), db=self.baser.claims.sdb)
            )
            if claim is None or not claim.currentSaid:
                return None
            return self.baser.statuses.deserializer(
                txn.get(claim.currentSaid.encode(), db=self.baser.statuses.sdb)
            )

    def next_pending(self):
        """Read the sole pending signed operation without changing its phase."""
        return self.baser.pending.get(keys=("writer",))

    def next_preparing(self):
        """Read interrupted preparation without consulting queued operations."""
        return self.baser.preparing.get(keys=("writer",))

    def next_queued(self):
        """Detach the oldest queued operation and close its reader before returning."""
        with (
            self.baser.env.begin(db=self.baser.queued.sdb) as txn,
            txn.cursor() as cursor,
        ):
            return (
                self.baser.queued.deserializer(bytes(cursor.value()))
                if cursor.first()
                else None
            )

    def operation_for(self, event_id):
        """Return a detached phase/record for one event, without history iteration."""
        statusEvent = self.baser.inbox.get(keys=(event_id,))
        if statusEvent is None or not statusEvent.operationId:
            return None, None
        for phase in ("queued", "preparing", "pending", "completed", "dead"):
            key = (
                "writer"
                if phase in ("preparing", "pending")
                else statusEvent.operationId
            )
            postingOperation = getattr(self.baser, phase).get(keys=(key,))
            if (
                postingOperation is not None
                and postingOperation.operationId == statusEvent.operationId
            ):
                return phase, postingOperation
        raise ValueError("stored event has no delivery phase")

    def inbox_scope(self):
        """Return the bound inbox scope, or None."""
        return self.baser.inbox_scope.get(keys=("receiver",))

    def posting_scope(self):
        """Return the bound posting scope, or None."""
        return self.baser.scope.get(keys=("posting",))

    def wallet_effects(self, wallet):
        """Return only this writer's confirmed wallet effects."""
        return self.baser.walletEffects.get(keys=(wallet,))

    def transaction(self, txHash):
        """Return one persisted signed transaction and its receipt state."""
        return self.baser.transactions.get(keys=(txHash,))

    def dead_operation(self, operationId):
        """Return one dead letter by operation ID."""
        return self.baser.dead.get(keys=(operationId,))

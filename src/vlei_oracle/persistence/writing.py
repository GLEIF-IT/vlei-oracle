"""Explicit atomic writes of prepared application records."""

from vlei_oracle.persistence.records import ReplayClaim


class OracleWriter:
    """Borrow an open store; serialize before opening short write transactions."""

    def __init__(self, baser):
        self.baser = baser

    def record_event(self, event):
        """Record an older event without changing selection or creating posting work."""
        db = self.baser
        key, raw = event.eventId.encode(), db.inbox.serializer(event)
        with db.env.begin(write=True) as txn:
            txn.put(key, raw, db=db.inbox.sdb)

    def record_status_and_queue(
        self, event, status, claim, previous, operation, sequence
    ):
        """Commit receipt, selected records and one queued operation together.

        All policy and record construction is complete before this call. The
        receiver runs synchronously in the sole application writer; no yield or
        external I/O separates its reads, preparation and this commit.
        """
        db = self.baser
        eventKey, eventRaw = event.eventId.encode(), db.inbox.serializer(event)
        statusKey = status.associationSaid.encode()
        statusRaw = db.statuses.serializer(status)
        claimKey = status.association["extId"].encode()
        claimRaw = db.claims.serializer(claim)
        previousKey = (
            previous.associationSaid.encode() if previous is not None else None
        )
        previousRaw = db.statuses.serializer(previous) if previous is not None else None
        operationKey = operation.operationId.encode()
        operationRaw = db.queued.serializer(operation)
        sequenceRaw = db.sequence.serializer(sequence)
        with db.env.begin(write=True) as txn:
            if previousKey is not None:
                txn.put(previousKey, previousRaw, db=db.statuses.sdb)
            txn.put(statusKey, statusRaw, db=db.statuses.sdb)
            txn.put(claimKey, claimRaw, db=db.claims.sdb)
            txn.put(operationKey, operationRaw, db=db.queued.sdb)
            txn.put(b"writer", sequenceRaw, db=db.sequence.sdb)
            txn.put(eventKey, eventRaw, db=db.inbox.sdb)

    def queued_to_preparing(self, postingOperation):
        """Claim the selected queued operation in the sole preparation slot."""
        key = postingOperation.operationId.encode()
        expected = self.baser.queued.serializer(postingOperation)
        raw = self.baser.preparing.serializer(postingOperation)
        with self.baser.env.begin(write=True) as txn:
            if txn.get(key, db=self.baser.queued.sdb) != expected:
                raise ValueError("queued operation is absent or changed")
            if txn.get(b"writer", db=self.baser.preparing.sdb) or txn.get(
                b"writer", db=self.baser.pending.sdb
            ):
                raise ValueError("writer already has active work")
            txn.delete(key, db=self.baser.queued.sdb)
            txn.put(b"writer", raw, db=self.baser.preparing.sdb)

    def preparing_to_pending(self, postingOperation, txRecord):
        """Journal the signed transaction and move preparing work to pending atomically."""
        source_key, target_key = b"writer", b"writer"
        raw = self.baser.pending.serializer(postingOperation)
        tx_raw = self.baser.transactions.serializer(txRecord)
        with self.baser.env.begin(write=True) as txn:
            currentOperation = self.baser.preparing.deserializer(
                txn.get(source_key, db=self.baser.preparing.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected preparing phase")
            self._put_transaction(txn, txRecord, tx_raw)
            txn.delete(source_key, db=self.baser.preparing.sdb)
            txn.put(target_key, raw, db=self.baser.pending.sdb)

    def preparing_to_completed(self, postingOperation):
        """Remove preparing work and retain its terminal publication outcome."""
        source_key, target_key = b"writer", postingOperation.operationId.encode()
        raw = self.baser.completed.serializer(postingOperation)
        with self.baser.env.begin(write=True) as txn:
            currentOperation = self.baser.preparing.deserializer(
                txn.get(source_key, db=self.baser.preparing.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected preparing phase")
            txn.delete(source_key, db=self.baser.preparing.sdb)
            txn.put(target_key, raw, db=self.baser.completed.sdb)

    def preparing_to_dead(self, postingOperation):
        """Stop unsigned preparation without blocking later queued operations."""
        source_key, target_key = b"writer", postingOperation.operationId.encode()
        raw = self.baser.dead.serializer(postingOperation)
        with self.baser.env.begin(write=True) as txn:
            currentOperation = self.baser.preparing.deserializer(
                txn.get(source_key, db=self.baser.preparing.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected preparing phase")
            txn.delete(source_key, db=self.baser.preparing.sdb)
            txn.put(target_key, raw, db=self.baser.dead.sdb)

    def pending_to_preparing(self, postingOperation, txRecord, walletEffects=None):
        """Record a resolved receipt and optional confirmed effects before the next step."""
        source_key, target_key = b"writer", b"writer"
        raw = self.baser.preparing.serializer(postingOperation)
        tx_raw = self.baser.transactions.serializer(txRecord)
        effects_raw = (
            self.baser.walletEffects.serializer(walletEffects)
            if walletEffects is not None
            else None
        )
        with self.baser.env.begin(write=True) as txn:
            currentOperation = self.baser.pending.deserializer(
                txn.get(source_key, db=self.baser.pending.sdb)
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
                    db=self.baser.walletEffects.sdb,
                )
            txn.delete(source_key, db=self.baser.pending.sdb)
            txn.put(target_key, raw, db=self.baser.preparing.sdb)

    def pending_to_dead(self, postingOperation, sequence, txRecord=None):
        """Stop pending work with its prepared writer fence and optional resolved receipt."""
        source_key, target_key = b"writer", postingOperation.operationId.encode()
        raw = self.baser.dead.serializer(postingOperation)
        tx_raw = (
            self.baser.transactions.serializer(txRecord)
            if txRecord is not None
            else None
        )
        sequence_raw = self.baser.sequence.serializer(sequence)
        with self.baser.env.begin(write=True) as txn:
            currentOperation = self.baser.pending.deserializer(
                txn.get(source_key, db=self.baser.pending.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected pending phase")
            if txRecord is not None:
                self._put_transaction(txn, txRecord, tx_raw)
            txn.delete(source_key, db=self.baser.pending.sdb)
            txn.put(target_key, raw, db=self.baser.dead.sdb)
            txn.put(b"writer", sequence_raw, db=self.baser.sequence.sdb)

    def dead_to_pending(self, postingOperation, sequence):
        """Resume the same signed transaction and clear its prepared writer fence."""
        source_key, target_key = postingOperation.operationId.encode(), b"writer"
        raw = self.baser.pending.serializer(postingOperation)
        sequence_raw = self.baser.sequence.serializer(sequence)
        with self.baser.env.begin(write=True) as txn:
            currentOperation = self.baser.dead.deserializer(
                txn.get(source_key, db=self.baser.dead.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected dead phase")
            if txn.get(b"writer", db=self.baser.preparing.sdb) or txn.get(
                b"writer", db=self.baser.pending.sdb
            ):
                raise ValueError("writer already has active work")
            txn.delete(source_key, db=self.baser.dead.sdb)
            txn.put(target_key, raw, db=self.baser.pending.sdb)
            txn.put(b"writer", sequence_raw, db=self.baser.sequence.sdb)

    def dead_to_preparing(self, postingOperation, sequence):
        """Resume unsigned preparation with the operator-reset attempt budget."""
        source_key, target_key = postingOperation.operationId.encode(), b"writer"
        raw = self.baser.preparing.serializer(postingOperation)
        sequence_raw = self.baser.sequence.serializer(sequence)
        with self.baser.env.begin(write=True) as txn:
            currentOperation = self.baser.dead.deserializer(
                txn.get(source_key, db=self.baser.dead.sdb)
            )
            if (
                currentOperation is None
                or currentOperation.operationId != postingOperation.operationId
            ):
                raise ValueError("operation is not in the expected dead phase")
            if txn.get(b"writer", db=self.baser.preparing.sdb) or txn.get(
                b"writer", db=self.baser.pending.sdb
            ):
                raise ValueError("writer already has active work")
            txn.delete(source_key, db=self.baser.dead.sdb)
            txn.put(target_key, raw, db=self.baser.preparing.sdb)
            txn.put(b"writer", sequence_raw, db=self.baser.sequence.sdb)

    def _put_transaction(self, txn, txRecord, raw):
        """Save journal progress while refusing mutation of previously signed bytes."""
        key = txRecord.txHash.encode()
        existing = self.baser.transactions.deserializer(
            txn.get(key, db=self.baser.transactions.sdb)
        )
        if existing is not None and any(
            getattr(existing, field) != getattr(txRecord, field)
            for field in ("rawTransaction", "nonce", "associationSaid", "functionName")
        ):
            raise ValueError("cannot change a persisted signed attempt")
        txn.put(key, raw, db=self.baser.transactions.sdb)

    def record_inbox_scope(self, scope):
        """Persist the prepared receiver deployment binding."""
        self.baser.inbox_scope.put(keys=("receiver",), val=scope)

    def record_posting_scope(self, scope):
        """Persist the prepared posting deployment binding."""
        self.baser.scope.pin(keys=("posting",), val=scope)

    def claim_nonce(self, peer, nonce, expires_at):
        """Insert one transport replay claim without overwriting a consumed key."""
        return self.baser.replay_claims.put(
            keys=(peer, nonce), val=ReplayClaim(expires_at)
        )

"""Posting persistence: bounded reads and explicit atomic writes."""


class PostingReader:
    """Borrow the open store for posting persistence only."""

    def __init__(self, baser):
        self.baser = baser

    def posting_sequence(self):
        """Return arrival order and the unresolved-signature fence, or None."""
        return self.baser.sequence.get(keys=("writer",))

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

    def wallet_effects(self, wallet):
        """Return only this writer's confirmed wallet effects."""
        return self.baser.walletEffects.get(keys=(wallet,))

    def transaction(self, txHash):
        """Return one persisted signed transaction and its receipt state."""
        return self.baser.transactions.get(keys=(txHash,))

    def dead_operation(self, operationId):
        """Return one dead letter by operation ID."""
        return self.baser.dead.get(keys=(operationId,))


class PostingWriter:
    """Borrow the open store for posting persistence only."""

    def __init__(self, baser):
        self.baser = baser

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

    def record_preparing_progress(self, postingOperation):
        """Persist prepared preparing progress without changing phase."""
        self.baser.preparing.pin(keys=("writer",), val=postingOperation)

    def record_pending_progress(self, postingOperation):
        """Persist prepared pending progress without changing phase."""
        self.baser.pending.pin(keys=("writer",), val=postingOperation)

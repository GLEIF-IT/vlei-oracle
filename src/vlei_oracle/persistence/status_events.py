"""Status-event persistence: explicit atomic writes."""


class StatusEventWriter:
    """Borrow the open store for status-event persistence only."""

    def __init__(self, baser):
        self.baser = baser

    def record_event(self, statusEvent):
        """Record an older event without changing selection or creating posting work."""
        db = self.baser
        key, raw = statusEvent.eventId.encode(), db.inbox.serializer(statusEvent)
        with db.env.begin(write=True) as txn:
            txn.put(key, raw, db=db.inbox.sdb)

    def record_and_queue(
        self,
        statusEvent,
        associationStatus,
        walletClaim,
        previousStatus,
        postingOperation,
        postingSequence,
    ):
        """Commit status event, selected records and one queued operation together.

        All policy and record construction is complete before this call. The
        receiver runs synchronously in the sole application writer; no yield or
        external I/O separates its reads, preparation and this commit.
        """
        db = self.baser
        eventKey, eventRaw = (
            statusEvent.eventId.encode(),
            db.inbox.serializer(statusEvent),
        )
        statusKey = associationStatus.associationSaid.encode()
        statusRaw = db.statuses.serializer(associationStatus)
        claimKey = associationStatus.association["extId"].encode()
        claimRaw = db.claims.serializer(walletClaim)
        previousKey = (
            previousStatus.associationSaid.encode()
            if previousStatus is not None
            else None
        )
        previousRaw = (
            db.statuses.serializer(previousStatus)
            if previousStatus is not None
            else None
        )
        operationKey = postingOperation.operationId.encode()
        operationRaw = db.queued.serializer(postingOperation)
        sequenceRaw = db.sequence.serializer(postingSequence)
        with db.env.begin(write=True) as txn:
            if previousKey is not None:
                txn.put(previousKey, previousRaw, db=db.statuses.sdb)
            txn.put(statusKey, statusRaw, db=db.statuses.sdb)
            txn.put(claimKey, claimRaw, db=db.claims.sdb)
            txn.put(operationKey, operationRaw, db=db.queued.sdb)
            txn.put(b"writer", sequenceRaw, db=db.sequence.sdb)
            txn.put(eventKey, eventRaw, db=db.inbox.sdb)

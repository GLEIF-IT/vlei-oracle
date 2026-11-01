"""Explicit atomic writes of prepared application records."""


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

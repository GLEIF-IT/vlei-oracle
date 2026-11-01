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

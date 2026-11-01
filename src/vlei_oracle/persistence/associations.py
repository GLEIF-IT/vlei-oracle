"""Associations persistence: bounded reads and explicit atomic writes."""


class AssociationReader:
    """Borrow the open store for associations persistence only."""

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

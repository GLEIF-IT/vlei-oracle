"""Persistent receiver and posting deployment bindings."""


class ScopeStore:
    """Borrow storage only for deployment bindings."""

    def __init__(self, baser):
        self.baser = baser

    def inbox_scope(self):
        """Return the bound inbox scope, or None."""
        return self.baser.inbox_scope.get(keys=("receiver",))

    def posting_scope(self):
        """Return the bound posting scope, or None."""
        return self.baser.scope.get(keys=("posting",))

    def record_inbox_scope(self, scope):
        """Persist the prepared receiver deployment binding."""
        self.baser.inbox_scope.put(keys=("receiver",), val=scope)

    def record_posting_scope(self, scope):
        """Persist the prepared posting deployment binding."""
        self.baser.scope.pin(keys=("posting",), val=scope)

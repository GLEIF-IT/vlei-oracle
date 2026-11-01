"""Last observed inbox and posting settings for startup warnings."""


class ScopeStore:
    """Borrow storage for advisory startup records only."""

    def __init__(self, baser):
        self.baser = baser

    def inbox(self):
        """Return the previous inbox settings, or None on first use."""
        return self.baser.inbox_scope.get(keys=("receiver",))

    def posting(self):
        """Return the previous posting settings, or None on first use."""
        return self.baser.scope.get(keys=("posting",))

    def save_inbox(self, scope):
        """Replace the inbox baseline without changing received status or selection."""
        self.baser.inbox_scope.pin(keys=("receiver",), val=scope)

    def save_posting(self, scope):
        """Replace the posting baseline without changing the journal or queues."""
        self.baser.scope.pin(keys=("posting",), val=scope)

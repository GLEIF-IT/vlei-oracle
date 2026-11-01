"""Application startup checks for persistent receiver and posting scopes."""


class ScopeService:
    """Validate configuration against detached bindings before recording scope."""

    def __init__(self, store):
        self.store = store

    def bind_inbox(self, scope):
        """Refuse a different verifier or chain before enabling receipt."""
        existing = self.store.inbox_scope()
        if existing is not None and existing != scope:
            raise ValueError("inbox belongs to another verifier or chain")
        self.store.bind_inbox(scope)

    def bind_posting(self, scope):
        """Refuse a different deployment before enabling transaction publication."""
        existing = self.store.posting_scope()
        if existing is not None and existing != scope:
            raise ValueError("store belongs to a different posting scope")
        self.store.bind_posting(scope)

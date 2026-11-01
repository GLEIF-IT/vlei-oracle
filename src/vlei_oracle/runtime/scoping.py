"""Application startup checks for persistent receiver and posting scopes."""


class ScopeService:
    """Validate configuration against detached bindings before recording scope."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def bind_inbox(self, scope):
        """Refuse a different verifier or chain before enabling receipt."""
        existing = self.reader.inbox_scope()
        if existing is not None and existing != scope:
            raise ValueError("inbox belongs to another verifier or chain")
        self.writer.record_inbox_scope(scope)

    def bind_posting(self, scope):
        """Refuse a different deployment before enabling transaction publication."""
        existing = self.reader.posting_scope()
        if existing is not None and existing != scope:
            raise ValueError("store belongs to a different posting scope")
        self.writer.record_posting_scope(scope)

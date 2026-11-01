"""Falcon adapter for read-only endorsement application queries."""

import falcon


class EndorsementEnd:
    """Translate local endorsement query results into HTTP responses."""

    def __init__(self, query):
        self.query = query

    def on_get(self, req, rep, said):
        """Return progress for one credential SAID, or 404 when unknown."""
        result = self.query.endorsement(said)
        if result is None:
            raise falcon.HTTPNotFound(title="Unknown endorsement")
        rep.media = result
        rep.set_header("Cache-Control", "no-store")

"""HIO scheduling adapter for the endorsement posting application service."""

from hio.base import doing


class PostingDoer(doing.DoDoer):
    """Schedule finite service steps beside the borrowed RPC effect clients."""

    def __init__(self, service, *, interval=1.0):
        """Borrow the service and its effects; interval is the pause between cycles."""
        self.service, self.interval = service, interval
        super().__init__(doers=[service.effects, doing.doify(self.post_do)])

    def post_do(self, tymth, tock=0.0, **opts):
        """Let receipt ingestion run between steps and during yielded RPC waits."""
        self.wind(tymth)
        yield tock
        while True:
            yield from self.service.cycle()
            yield self.interval

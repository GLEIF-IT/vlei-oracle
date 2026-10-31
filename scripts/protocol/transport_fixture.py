"""CESR delivery and HTTP status assertions for local exercises."""

import json

from hio.base import doing
from hio.core import http
from keri.app import httping


class PresentationDoer(doing.DoDoer):
    """Deliver CESR evidence and poll one oracle operation to an expected state.

    Owns its HTTP client and yields while transport and server processing run.
    HTTP acceptance confirms delivery; the status response confirms verification.
    ``result`` is populated only after the expected state is observed.
    """

    def __init__(self, *, port, recipient, stream, said, expected):
        self.client = http.Client(hostname="127.0.0.1", port=port)
        self.client_doer = http.ClientDoer(client=self.client)
        self.recipient, self.stream = recipient, stream
        self.said, self.expected = said, expected
        self.result = None
        super().__init__(doers=[self.client_doer, doing.doify(self.run)])

    def run(self, tymth, tock=0.0, **opts):
        """Send all messages, consume their responses, then poll with fresh headers."""
        yield tock
        # KERIpy streamCESRRequests requires at least one frame. Restart checks
        # intentionally poll existing work without presenting another Grant.
        count = (
            httping.streamCESRRequests(self.client, bytearray(self.stream), self.recipient)
            if self.stream else 0
        )
        for _ in range(count):
            while not self.client.responses:
                yield 0.01
            response = self.client.responses.popleft()
            assert response["status"] in (200, 202, 204), response
        for _ in range(100):
            # HIO retains headers by default; GET must drop the CESR POST headers.
            self.client.request(
                method="GET", path=f"/associations/{self.said}", headers={}
            )
            while not self.client.responses:
                yield 0.01
            response = self.client.responses.popleft()
            result = json.loads(response["body"])
            if response["status"] == 200:
                if result["state"] == self.expected:
                    self.result = result
                    self.remove([self.client_doer])
                    return True
                terminal = (
                    {"failed", "withdrawn"} if self.expected == "endorsed"
                    else {"failed"} if self.expected == "withdrawn"
                    else {"failed", "verified"}
                )
                assert result["state"] not in terminal, result
            yield 0.05
        raise AssertionError(f"Expected {self.expected}: {result}")

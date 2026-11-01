"""Native OOBI resolution for the explicitly authorized Verifier AID."""

from keri.app import oobiing
from keri.db import basing
from keri.help import helping


class VerifierOobi:
    """Borrow the Habery and refresh the configured introduction on each startup.

    KERIpy validates the returned KEL. Its resolved marker alone is insufficient:
    the returned identifier must match the configured producer and have accepted
    key state. Resolution never authorizes another identifier.
    """

    def __init__(self, hby, url, aid):
        """Queue a fresh native resolution, following KLI resolve --force semantics."""
        self.hby, self.url, self.aid = hby, url, aid
        hby.db.roobi.rem(keys=(url,))
        hby.db.oobis.pin(keys=(url,), val=basing.OobiRecord(date=helping.nowIso8601()))

    def ready(self):
        """Read the detached resolution outcome and accepted expected key state."""
        record = self.hby.db.roobi.get(keys=(self.url,))
        return bool(
            record
            and record.state == oobiing.Result.resolved
            and record.cid == self.aid
            and self.aid in self.hby.kevers
        )

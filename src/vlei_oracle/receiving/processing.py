"""Schedule native KERI parsing and escrow processing on HIO."""

from hio.base import doing
from hio.help import decking


class OracleAgent(doing.DoDoer):
    """Run parser and escrow processors required for direct-mode KERI traffic.

    ``msg_do`` consumes inbound bytes from the Falcon direct-mode endpoint.
    ``escrow_do`` gives KEL, TEL, reply, credential, and EXN escrows time to
    clear without introducing any non-HIO sleep or async model.
    """

    def __init__(self, *, parser, kvy, tvy, rvy, verifier, exc, cues=None, **opts):
        self.parser = parser
        self.kvy = kvy
        self.tvy = tvy
        self.rvy = rvy
        self.verifier = verifier
        self.exc = exc
        self.cues = cues if cues is not None else decking.Deck()
        super().__init__(
            doers=[doing.doify(self.msg_do), doing.doify(self.escrow_do)], **opts
        )

    def msg_do(self, tymth=None, tock=0.0, **kwa):
        """Feed queued HTTP direct-mode bytes into the KERI parser."""

        _ = kwa
        self.wind(tymth)
        self.tock = tock
        _ = yield self.tock
        done = yield from self.parser.parsator(local=True)
        return done

    def escrow_do(self, tymth=None, tock=0.25, **kwa):
        """Periodically process KERI, TEL, verifier, and EXN escrows."""

        _ = kwa
        self.wind(tymth)
        self.tock = tock
        _ = yield self.tock
        while True:
            self.kvy.processEscrows()
            self.rvy.processEscrowReply()
            if self.tvy is not None:
                self.tvy.processEscrows()
            if self.verifier is not None:
                self.verifier.processEscrows()
            self.exc.processEscrow()
            yield self.tock

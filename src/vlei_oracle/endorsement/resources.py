"""Read-only REST representation of the Oracle's own endorsement delivery state."""

from dataclasses import asdict

import falcon

from vlei_oracle.persistence.records import AppliedWallet, PostingSequence


class EndorsementEnd:
    """Expose durable progress without Ethereum calls or mutation permissions."""

    def __init__(self, baser):
        self.baser = baser

    def on_get(self, req, rep, said):
        """Return a known association's latest operation, or 404 for an unknown SAID.

        confirmedEffect describes this writer's last confirmed wallet effects,
        not current chain truth. Public responses omit signed transaction bytes.
        """
        status = self.baser.statuses.get(keys=(said,))
        if status is None:
            raise falcon.HTTPNotFound(title="Unknown endorsement")
        phase, work = self.baser.operation_for(f"{said}:{status.statusRevision}")
        applied = self.baser.applied.get(keys=(status.wallet,)) or AppliedWallet()
        sequence = self.baser.sequence.get(keys=("writer",)) or PostingSequence()
        rep.media = {
            "associationSaid": said,
            "wallet": status.wallet,
            "verifierStatus": status.status,
            "selection": status.selection,
            "phase": phase,
            "operation": asdict(work) if work else None,
            "confirmedEffect": asdict(applied),
            "writerBlockedBy": sequence.blockedBy or None,
        }
        rep.set_header("Cache-Control", "no-store")

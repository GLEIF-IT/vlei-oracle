"""Read-only REST representation of the Oracle's own endorsement delivery state."""

from vlei_oracle.persistence.reading import OracleReader

from dataclasses import asdict

import falcon

from vlei_oracle.persistence.records import WalletEffects, PostingSequence


class EndorsementEnd:
    """Expose durable progress without Ethereum calls or mutation permissions."""

    def __init__(self, baser):
        self.baser = baser

    def on_get(self, req, rep, said):
        """Return a known association's latest operation, or 404 for an unknown SAID.

        confirmedEffect describes this writer's last confirmed wallet effects,
        not current chain truth. Public responses omit signed transaction bytes.
        """
        associationStatus = self.baser.statuses.get(keys=(said,))
        if associationStatus is None:
            raise falcon.HTTPNotFound(title="Unknown endorsement")
        phase, postingOperation = OracleReader(self.baser).operation_for(
            f"{said}:{associationStatus.statusRevision}"
        )
        walletEffects = (
            self.baser.walletEffects.get(keys=(associationStatus.wallet,))
            or WalletEffects()
        )
        sequence = self.baser.sequence.get(keys=("writer",)) or PostingSequence()
        rep.media = {
            "associationSaid": said,
            "wallet": associationStatus.wallet,
            "verifierStatus": associationStatus.status,
            "selection": associationStatus.selection,
            "phase": phase,
            "operation": asdict(postingOperation) if postingOperation else None,
            "confirmedEffect": asdict(walletEffects),
            "writerBlockedBy": sequence.blockedBy or None,
        }
        rep.set_header("Cache-Control", "no-store")

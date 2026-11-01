"""Read-only application projection of local endorsement delivery progress."""

from dataclasses import asdict

from vlei_oracle.persistence.records import PostingSequence, WalletEffects


class EndorsementQuery:
    """Read bounded local records; never consult Ethereum or expose signed bytes."""

    def __init__(self, reader):
        self.reader = reader

    def endorsement(self, said):
        """Return public progress for one credential SAID, or None if unknown.

        Confirmed effects describe this writer's recorded results, not a fresh
        assertion about current on-chain state.
        """
        associationStatus = self.reader.association(said)
        if associationStatus is None:
            return None
        phase, postingOperation = self.reader.operation_for(
            f"{said}:{associationStatus.statusRevision}"
        )
        walletEffects = (
            self.reader.wallet_effects(associationStatus.wallet) or WalletEffects()
        )
        sequence = self.reader.posting_sequence() or PostingSequence()
        return {
            "associationSaid": said,
            "wallet": associationStatus.wallet,
            "verifierStatus": associationStatus.status,
            "selection": associationStatus.selection,
            "phase": phase,
            "operation": asdict(postingOperation) if postingOperation else None,
            "confirmedEffect": asdict(walletEffects),
            "writerBlockedBy": sequence.blockedBy or None,
        }

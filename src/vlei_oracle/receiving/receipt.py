"""Application receipt policy for authenticated, parsed verifier status events."""

from vlei_oracle.associations.records import WalletClaim
from vlei_oracle.associations.selecting import update_selection
from vlei_oracle.persistence.records import PostingOperation, PostingSequence


class ReceiptService:
    """Prepare receipt and selection using detached reads, then commit once.

    The caller authenticates and parses first and ACKs only after return. This
    synchronous service never yields between reads and commit. The runtime owns
    one application writer; it does not support concurrent receiver processes
    sharing a store. No database transaction encloses policy decisions.
    """

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def receive_status(self, event, status):
        """Apply monotonic status and wallet selection, or acknowledge an old event.

        Exact redelivery is harmless; changed event bytes or issuance facts are
        conflicts. Credential revocation is terminal for this association SAID.
        """
        prior = self.reader.event(event.eventId)
        if prior is not None:
            if prior.body != event.body:
                raise ValueError("conflicting event revision")
            return
        current = self.reader.association(status.associationSaid)
        if current is not None:
            if current.association != status.association:
                raise ValueError("association facts cannot change")
            if (
                current.status == "revoked"
                and status.status != "revoked"
                and status.statusRevision > current.statusRevision
            ):
                raise ValueError("revoked association cannot be reaccepted")
            if (
                current.status == "accepted"
                and status.status == "revoked"
                and status.statusRevision < current.statusRevision
            ):
                raise ValueError("revocation revision precedes acceptance")
            if status.statusRevision <= current.statusRevision:
                self.writer.record_event(event)
                return
            status.selection = current.selection
        claim = self.reader.claim(status.association["extId"]) or WalletClaim()
        selected = (
            self.reader.association(claim.currentSaid) if claim.currentSaid else None
        )
        update_selection(status, current=selected, claim=claim)
        previous = (
            selected
            if selected is not None
            and selected.associationSaid != status.associationSaid
            else None
        )
        sequence = self.reader.posting_sequence() or PostingSequence()
        sequence.next += 1
        event.operationId = f"{sequence.next:016x}"
        operation = PostingOperation(
            event.operationId, event.eventId, status.associationSaid, status.wallet
        )
        self.writer.record_status_and_queue(
            event, status, claim, previous, operation, sequence
        )

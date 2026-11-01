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

    def __init__(self, reader, writer, posting_reader):
        """Borrow association reads, atomic receipt writes and sequence reads."""
        self.reader, self.writer = reader, writer
        self.posting_reader = posting_reader

    def receive_status(self, statusEvent, associationStatus):
        """Apply monotonic status and wallet selection, or acknowledge an old event.

        Exact redelivery is harmless; changed event bytes or issuance facts are
        conflicts. Credential revocation is terminal for this association SAID.
        """
        priorEvent = self.reader.event(statusEvent.eventId)
        if priorEvent is not None:
            if priorEvent.body != statusEvent.body:
                raise ValueError("conflicting event revision")
            return
        currentStatus = self.reader.association(associationStatus.associationSaid)
        if currentStatus is not None:
            if currentStatus.association != associationStatus.association:
                raise ValueError("association facts cannot change")
            if (
                currentStatus.status == "revoked"
                and associationStatus.status != "revoked"
                and associationStatus.statusRevision > currentStatus.statusRevision
            ):
                raise ValueError("revoked association cannot be reaccepted")
            if (
                currentStatus.status == "accepted"
                and associationStatus.status == "revoked"
                and associationStatus.statusRevision < currentStatus.statusRevision
            ):
                raise ValueError("revocation revision precedes acceptance")
            if associationStatus.statusRevision <= currentStatus.statusRevision:
                self.writer.record_event(statusEvent)
                return
            associationStatus.selection = currentStatus.selection
        walletClaim = (
            self.reader.claim(associationStatus.association["extId"]) or WalletClaim()
        )
        selectedStatus = (
            self.reader.association(walletClaim.currentSaid)
            if walletClaim.currentSaid
            else None
        )
        update_selection(associationStatus, current=selectedStatus, claim=walletClaim)
        previousStatus = (
            selectedStatus
            if selectedStatus is not None
            and selectedStatus.associationSaid != associationStatus.associationSaid
            else None
        )
        postingSequence = self.posting_reader.seq() or PostingSequence()
        postingSequence.next += 1
        statusEvent.operationId = f"{postingSequence.next:016x}"
        postingOperation = PostingOperation(
            statusEvent.operationId,
            statusEvent.eventId,
            associationStatus.associationSaid,
            associationStatus.wallet,
        )
        self.writer.record_and_queue(
            statusEvent,
            associationStatus,
            walletClaim,
            previousStatus,
            postingOperation,
            postingSequence,
        )

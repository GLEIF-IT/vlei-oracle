"""Offline application recovery of one corrected posting dead letter."""

from dataclasses import replace

from vlei_oracle.persistence.records import PostingSequence


class RecoveryService:
    """Prepare one explicit recovery transition; the operator stops the service first."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def retry_operation(self, operation_id):
        """Reset the attempt budget, retaining signed bytes and respecting writer fences."""
        if self.reader.next_prep() or self.reader.next_pending():
            raise ValueError("writer already has active work")
        sequence = self.reader.seq() or PostingSequence()
        if sequence.blockedBy and sequence.blockedBy != operation_id:
            raise ValueError("another unresolved signed operation blocks the writer")
        postingOperation = self.reader.dead(operation_id)
        if postingOperation is None:
            raise ValueError("unknown dead-letter operation")
        postingOperation = replace(
            postingOperation,
            attempts=0,
            rpcFailures=0,
            error="",
            signedAt=0.0,
            submitted=False,
        )
        sequence = replace(sequence, blockedBy="")
        if postingOperation.txHash:
            self.writer.dead_to_pending(postingOperation, sequence)
        else:
            self.writer.dead_to_prep(postingOperation, sequence)

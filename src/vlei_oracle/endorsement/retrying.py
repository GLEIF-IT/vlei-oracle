"""Offline recovery of one corrected posting dead letter."""

from dataclasses import replace

from vlei_oracle.persistence.records import PostingSequence


def retry_operation(baser, operation_id):
    """Resume one dead letter with the service stopped, retaining signed bytes.

    Read and prepare recovery before the fixed dead-to-pending or dead-to-preparing
    write. An unresolved signature belonging to another operation fences recovery.
    """
    if baser.next_preparing() or baser.next_pending():
        raise ValueError("writer already has active work")
    sequence = baser.sequence.get(keys=("writer",)) or PostingSequence()
    if sequence.blockedBy and sequence.blockedBy != operation_id:
        raise ValueError("another unresolved signed operation blocks the writer")
    postingOperation = baser.dead.get(keys=(operation_id,))
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
        baser.dead_to_pending(postingOperation, sequence)
    else:
        baser.dead_to_preparing(postingOperation, sequence)

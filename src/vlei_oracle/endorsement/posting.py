"""Advance one durable endorsement operation through preparation and confirmation.

Association authority flows from the verifier. This worker never discovers work
or repairs drift by reading contract mappings; completed operations stay terminal.
"""

import logging
import time
from dataclasses import asdict

from eth_account import Account
from hio.base import doing
from web3 import Web3

from vlei_oracle.endorsement.decisions import ZERO_IDENTITY, next_contract_call
from vlei_oracle.persistence.records import (
    PostingScope,
    TransactionRecord,
    WalletEffects,
)

logger = logging.getLogger(__name__)


class StatusPoster(doing.DoDoer):
    """Borrow the store and serialize one writer's explicit delivery obligations.

    max_attempts bounds each contract step and interrupted pending RPCs.
    pending_timeout bounds receipt waiting in wall-clock seconds across restarts.
    Unresolved signed dead letters fence the writer; retry uses the same bytes.
    """

    def __init__(
        self,
        *,
        baser,
        effects,
        key,
        interval=1.0,
        max_attempts=3,
        pending_timeout=120.0,
        clock=time.time,
    ):
        self.baser, self.effects = baser, effects
        self.account = Account.from_key(key)
        self.interval, self.max_attempts = interval, max_attempts
        self.pending_timeout, self.clock = pending_timeout, clock
        if max_attempts < 1 or pending_timeout <= 0:
            raise ValueError("posting retry limits must be positive")
        if self.account.address.lower() != effects.writer.lower():
            raise ValueError("posting key does not match writer")
        baser.bind_posting_scope(
            PostingScope(
                effects.chain_id,
                effects.writer.lower(),
                effects.contracts["endorsement"].address.lower(),
                effects.contracts["identity"].address.lower(),
            )
        )
        super().__init__(doers=[effects, doing.doify(self.post_do)])

    def post_do(self, tymth, tock=0.0, **opts):
        """Allow receipt ingestion between finite, one-operation posting steps."""
        self.wind(tymth)
        yield tock
        while True:
            yield from self.cycle()
            yield self.interval

    def contract_call(self, postingOperation):
        """Choose from current off-chain selection and our own confirmed effects."""
        associationStatus = self.baser.statuses.get(
            keys=(postingOperation.associationSaid,)
        )
        selectedStatus = self.baser.selected_association(
            associationStatus.association["extId"]
        )
        walletEffects = (
            self.baser.walletEffects.get(keys=(postingOperation.wallet,))
            or WalletEffects()
        )
        valid = (
            associationStatus.status == "accepted"
            and selectedStatus is not None
            and selectedStatus.associationSaid == associationStatus.associationSaid
        )
        if selectedStatus is None and walletEffects.associationSaid:
            # A newer retirement can supersede an older published claim before
            # its acceptance arrives. Release the wallet's own recorded effect.
            return (
                "endorsement",
                "withdrawEndorsement",
                [postingOperation.wallet, walletEffects.associationSaid],
            )
        return next_contract_call(
            asdict(associationStatus),
            identity=walletEffects.identityRef or ZERO_IDENTITY,
            endorsement=(
                walletEffects.endorsementIdentity or ZERO_IDENTITY,
                walletEffects.associationSaid,
            ),
            valid=valid,
        )

    def cycle(self):
        """Advance only pending work, or prepare one step of the oldest operation."""
        postingOperation = self.baser.pending.get(keys=("writer",))
        if postingOperation is not None:
            yield from self.confirm(postingOperation)
            return
        postingOperation = self.baser.begin_operation()
        if postingOperation is None:
            return
        contractCall = self.contract_call(postingOperation)
        if contractCall is None:
            associationStatus = self.baser.statuses.get(
                keys=(postingOperation.associationSaid,)
            )
            postingOperation.outcome = (
                "published"
                if associationStatus.selection == "selected"
                else associationStatus.selection
            )
            self.baser.move_operation(postingOperation, "preparing", "completed")
            return
        if postingOperation.attempts >= self.max_attempts:
            postingOperation.error = (
                postingOperation.error
                or "preparation interrupted; attempt limit reached"
            )
            self.baser.move_operation(postingOperation, "preparing", "dead")
            return
        postingOperation.attempts += 1
        postingOperation.functionName = contractCall[1]
        self.baser.preparing.pin(keys=("writer",), val=postingOperation)
        try:
            unsignedTransaction = yield from self.effects.prepare(contractCall)
            if self.contract_call(postingOperation) != contractCall:
                # No signature exists: a newer local event may safely suppress this step.
                postingOperation.attempts = 0
                postingOperation.error = ""
                self.baser.preparing.pin(keys=("writer",), val=postingOperation)
                return
            signedTransaction = self.account.sign_transaction(unsignedTransaction)
        except (OSError, TimeoutError, ValueError) as error:
            self.fail(postingOperation, "preparing", error)
            return
        postingOperation.txHash = Web3.to_hex(signedTransaction.hash)
        postingOperation.signedAt = self.clock()
        postingOperation.error = ""
        txRecord = TransactionRecord(
            txHash=postingOperation.txHash,
            rawTransaction=Web3.to_hex(signedTransaction.raw_transaction),
            nonce=unsignedTransaction["nonce"],
            associationSaid=postingOperation.associationSaid,
            functionName=postingOperation.functionName,
        )
        self.baser.move_operation(
            postingOperation, "preparing", "pending", txRecord=txRecord
        )

    def confirm(self, postingOperation):
        """Observe only this signed transaction, submitting the same bytes if needed."""
        txRecord = self.baser.transactions.get(keys=(postingOperation.txHash,))
        if not postingOperation.signedAt:
            postingOperation.signedAt = self.clock()
            self.baser.pending.pin(keys=("writer",), val=postingOperation)
        try:
            yield from self.effects.check_chain()
            receipt = yield from self.effects.receipt(postingOperation.txHash)
            if receipt is not None and receipt["confirmed"]:
                self.complete_step(
                    postingOperation, txRecord, receipt["status"] == "0x1"
                )
                return
            if self.clock() - postingOperation.signedAt >= self.pending_timeout:
                postingOperation.error = (
                    "receipt deadline exceeded; signed transaction remains unresolved"
                )
                self.baser.move_operation(postingOperation, "pending", "dead")
                return
            if not postingOperation.submitted and receipt is None:
                yield from self.effects.submit(txRecord.rawTransaction, txRecord.txHash)
                postingOperation.submitted = True
                postingOperation.error = ""
                self.baser.pending.pin(keys=("writer",), val=postingOperation)
        except (OSError, TimeoutError, ValueError) as error:
            self.fail(postingOperation, "pending", error)

    def complete_step(self, postingOperation, txRecord, succeeded):
        """Atomically record a confirmed effect and return to the next preparation."""
        txRecord.state = "confirmed" if succeeded else "reverted"
        postingOperation.lastTransactionHash = txRecord.txHash
        postingOperation.txHash = ""
        postingOperation.submitted = False
        postingOperation.signedAt = 0.0
        postingOperation.rpcFailures = 0
        walletEffects = None
        if succeeded:
            associationStatus = self.baser.statuses.get(
                keys=(postingOperation.associationSaid,)
            )
            walletEffects = (
                self.baser.walletEffects.get(keys=(postingOperation.wallet,))
                or WalletEffects()
            )
            if postingOperation.functionName == "registerIdentity":
                walletEffects.identityRef = associationStatus.identityRef
            elif postingOperation.functionName == "removeIdentity":
                walletEffects.identityRef = ""
            elif postingOperation.functionName == "publishEndorsement":
                walletEffects.endorsementIdentity = associationStatus.identityRef
                walletEffects.associationSaid = associationStatus.associationSaid
            elif postingOperation.functionName == "withdrawEndorsement":
                walletEffects.endorsementIdentity = walletEffects.associationSaid = ""
            else:
                raise ValueError("unknown persisted contract action")
            postingOperation.attempts = 0
            postingOperation.error = ""
        else:
            postingOperation.error = f"transaction reverted: {txRecord.txHash}"
        phase = (
            "dead"
            if not succeeded and postingOperation.attempts >= self.max_attempts
            else "preparing"
        )
        self.baser.move_operation(
            postingOperation,
            "pending",
            phase,
            walletEffects=walletEffects,
            txRecord=txRecord,
        )

    def fail(self, postingOperation, phase, error):
        """Persist bounded failure without changing credential validity or selection."""
        postingOperation.error = str(error) or type(error).__name__
        logger.error("contract posting failed: %s", postingOperation.error)
        if phase == "pending":
            postingOperation.rpcFailures += 1
        exhausted = (
            postingOperation.rpcFailures
            if phase == "pending"
            else postingOperation.attempts
        ) >= self.max_attempts
        if exhausted:
            self.baser.move_operation(postingOperation, phase, "dead")
        else:
            getattr(self.baser, phase).pin(keys=("writer",), val=postingOperation)

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

from vlei_oracle.endorsement.decisions import ZERO_IDENTITY, next_action
from vlei_oracle.persistence.records import (
    AppliedWallet,
    PostingScope,
    TransactionRecord,
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

    def action(self, work):
        """Choose from current off-chain selection and our own confirmed effects."""
        status = self.baser.statuses.get(keys=(work.associationSaid,))
        selected = self.baser.selected_association(status.association["extId"])
        applied = self.baser.applied.get(keys=(work.wallet,)) or AppliedWallet()
        valid = (
            status.status == "accepted"
            and selected is not None
            and selected.associationSaid == status.associationSaid
        )
        if selected is None and applied.associationSaid:
            # A newer retirement can supersede an older published claim before
            # its acceptance arrives. Release the wallet's own recorded effect.
            return (
                "endorsement",
                "withdrawEndorsement",
                [work.wallet, applied.associationSaid],
            )
        return next_action(
            asdict(status),
            identity=applied.identityRef or ZERO_IDENTITY,
            endorsement=(
                applied.endorsementIdentity or ZERO_IDENTITY,
                applied.associationSaid,
            ),
            valid=valid,
        )

    def cycle(self):
        """Advance only pending work, or prepare one step of the oldest operation."""
        work = self.baser.pending.get(keys=("writer",))
        if work is not None:
            yield from self.confirm(work)
            return
        work = self.baser.begin_operation()
        if work is None:
            return
        action = self.action(work)
        if action is None:
            status = self.baser.statuses.get(keys=(work.associationSaid,))
            work.outcome = (
                "published" if status.selection == "selected" else status.selection
            )
            self.baser.move_operation(work, "preparing", "completed")
            return
        if work.attempts >= self.max_attempts:
            work.error = work.error or "preparation interrupted; attempt limit reached"
            self.baser.move_operation(work, "preparing", "dead")
            return
        work.attempts += 1
        work.action = action[1]
        self.baser.preparing.pin(keys=("writer",), val=work)
        try:
            tx = yield from self.effects.prepare(action)
            if self.action(work) != action:
                # No signature exists: a newer local event may safely suppress this step.
                work.attempts = 0
                work.error = ""
                self.baser.preparing.pin(keys=("writer",), val=work)
                return
            signed = self.account.sign_transaction(tx)
        except (OSError, TimeoutError, ValueError) as error:
            self.fail(work, "preparing", error)
            return
        work.txHash = Web3.to_hex(signed.hash)
        work.signedAt = self.clock()
        work.error = ""
        intent = TransactionRecord(
            txHash=work.txHash,
            rawTransaction=Web3.to_hex(signed.raw_transaction),
            nonce=tx["nonce"],
            associationSaid=work.associationSaid,
            action=work.action,
        )
        self.baser.move_operation(work, "preparing", "pending", intent=intent)

    def confirm(self, work):
        """Observe only this signed transaction, submitting the same bytes if needed."""
        intent = self.baser.transactions.get(keys=(work.txHash,))
        if not work.signedAt:
            work.signedAt = self.clock()
            self.baser.pending.pin(keys=("writer",), val=work)
        try:
            yield from self.effects.check_chain()
            receipt = yield from self.effects.receipt(work.txHash)
            if receipt is not None and receipt["confirmed"]:
                self.complete_step(work, intent, receipt["status"] == "0x1")
                return
            if self.clock() - work.signedAt >= self.pending_timeout:
                work.error = (
                    "receipt deadline exceeded; signed transaction remains unresolved"
                )
                self.baser.move_operation(work, "pending", "dead")
                return
            if not work.submitted and receipt is None:
                yield from self.effects.submit(intent.rawTransaction, intent.txHash)
                work.submitted = True
                work.error = ""
                self.baser.pending.pin(keys=("writer",), val=work)
        except (OSError, TimeoutError, ValueError) as error:
            self.fail(work, "pending", error)

    def complete_step(self, work, intent, succeeded):
        """Atomically record a confirmed effect and return to the next preparation."""
        intent.state = "confirmed" if succeeded else "reverted"
        work.lastTransactionHash = intent.txHash
        work.txHash = ""
        work.submitted = False
        work.signedAt = 0.0
        work.rpcFailures = 0
        applied = None
        if succeeded:
            status = self.baser.statuses.get(keys=(work.associationSaid,))
            applied = self.baser.applied.get(keys=(work.wallet,)) or AppliedWallet()
            if work.action == "registerIdentity":
                applied.identityRef = status.identityRef
            elif work.action == "removeIdentity":
                applied.identityRef = ""
            elif work.action == "publishEndorsement":
                applied.endorsementIdentity = status.identityRef
                applied.associationSaid = status.associationSaid
            elif work.action == "withdrawEndorsement":
                applied.endorsementIdentity = applied.associationSaid = ""
            else:
                raise ValueError("unknown persisted contract action")
            work.attempts = 0
            work.error = ""
        else:
            work.error = f"transaction reverted: {intent.txHash}"
        phase = (
            "dead"
            if not succeeded and work.attempts >= self.max_attempts
            else "preparing"
        )
        self.baser.move_operation(
            work, "pending", phase, applied=applied, intent=intent
        )

    def fail(self, work, phase, error):
        """Persist bounded failure without changing credential validity or selection."""
        work.error = str(error) or type(error).__name__
        logger.error("contract posting failed: %s", work.error)
        if phase == "pending":
            work.rpcFailures += 1
        exhausted = (
            work.rpcFailures if phase == "pending" else work.attempts
        ) >= self.max_attempts
        if exhausted:
            self.baser.move_operation(work, phase, "dead")
        else:
            getattr(self.baser, phase).pin(keys=("writer",), val=work)

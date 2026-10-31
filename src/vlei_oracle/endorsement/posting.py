"""Post trusted stored status through ordinary contract operations."""

import logging
from dataclasses import asdict

from eth_account import Account
from hio.base import doing
from web3 import Web3

from vlei_oracle.basing import PostingScope, TransactionRecord
from vlei_oracle.endorsement.decisions import next_action

logger = logging.getLogger(__name__)


class StatusPoster(doing.DoDoer):
    """Borrow the inbox; own one contract client and serialize the writer's sends.

    Receipt polling resumes from the existing journal after restart. An uncertain
    broadcast remains pending for operator inspection; this worker does not
    cancel, replace, or automatically rebroadcast transactions. Credential evidence
    and account observations are never refreshed here.
    """

    def __init__(self, *, baser, effects, key, interval=1.0):
        self.baser, self.effects = baser, effects
        self.account = Account.from_key(key)
        self.interval = interval
        if self.account.address.lower() != effects.writer.lower():
            raise ValueError("posting key does not match writer")
        # The two runtime compositions must not share a legacy writer journal.
        if baser.managed.cntAll() or baser.verifications.cntAll():
            raise ValueError("use an independent trusted-status store, not a legacy workflow store")
        baser.bind_posting_scope(PostingScope(
            effects.chain_id, effects.writer.lower(),
            effects.contracts["endorsement"].address.lower(),
            effects.contracts["identity"].address.lower(),
        ))
        super().__init__(doers=[effects, doing.doify(self.post_do)])

    def post_do(self, tymth, tock=0.0, **opts):
        """Poll once per interval, retaining failures without stopping HTTP receipt."""
        self.wind(tymth)
        yield tock
        while True:
            try:
                yield from self.cycle()
            except (OSError, TimeoutError, ValueError) as error:
                logger.error("contract posting failed: %s", error)
            yield self.interval

    def action(self, status, identity, endorsement):
        """Use current durable policy, never an earlier pre-RPC status snapshot."""
        current = self.baser.statuses.get(keys=(status.associationSaid,))
        selected = self.baser.selected_association(status.association["extId"])
        valid = current.status == "accepted" and selected is not None and selected.associationSaid == current.associationSaid
        return next_action(asdict(current), identity=identity, endorsement=endorsement, valid=valid)

    def cycle(self):
        """Resolve the outstanding send or submit at most one new contract action.

        A successful receipt records transaction success, not present mapping
        state. Subsequent observations determine whether the wallet is reconciled.
        Reverted sends remain in the journal; a later cycle may retry the current
        desired action with a fresh transaction.
        """
        pending = self.baser.unfinished_transaction()
        if pending is not None:
            yield from self.effects.check_chain()
            receipt = yield from self.effects.receipt(pending.txHash)
            if receipt is not None and receipt["confirmed"]:
                self.baser.complete_transaction(pending, pending.txHash, receipt["status"] == "0x1")
                if receipt["status"] == "0x0":
                    logger.error("transaction reverted: %s", pending.txHash)
            return

        # Iterate durable records, not notification cues: restart and duplicate
        # delivery require no separate queue or reconstructed verification work.
        for _, status in self.baser.statuses.getItemIter():
            identity, endorsement = yield from self.effects.observe(status.wallet)
            action = self.action(status, identity, endorsement)
            if action is None:
                continue
            tx = yield from self.effects.prepare(action)
            # HIO RPC waits yield to the inbox: replacement/revocation may
            # have changed the required action while preparing this transaction.
            if self.action(status, identity, endorsement) != action:
                return
            signed = self.account.sign_transaction(tx)
            intent = self.baser.pin_transaction(TransactionRecord(
                txHash=Web3.to_hex(signed.hash),
                rawTransaction=Web3.to_hex(signed.raw_transaction),
                nonce=tx["nonce"], gasPrice=tx["gasPrice"],
                associationSaid=status.associationSaid, action=action[1],
            ))
            try:
                yield from self.effects.submit(intent.rawTransaction, intent.txHash)
            except (OSError, TimeoutError, ValueError) as error:
                intent.failureReason = f"broadcast uncertain: {error}"
                self.baser.pin_transaction(intent)
                raise
            logger.info("submitted %s: %s", intent.action, intent.txHash)
            return

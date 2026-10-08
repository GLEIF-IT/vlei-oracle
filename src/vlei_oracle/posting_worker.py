"""Reconcile durable endorsement work and signed identities on the HIO clock."""

from eth_account import Account
from hio.base import doing
from keri import kering
from keri.core import coring
from keri.db import dbing
from web3 import Web3

from .association_verifier import verify_association
from .basing import STATE_ENDORSED, STATE_WITHDRAWN, PostingScope, TransactionRecord
from .eoa_worker import EoaVerifierDoer
from .errors import MissingCredentialArtifactsError, VerificationError
from .posting import next_action


def issuance_position(rgy, said):
    """Read the immutable issuance anchor, rather than the latest revocation."""
    creder = rgy.reger.creds.get(keys=(said,))
    dig = rgy.reger.getTel(dbing.snKey(said, 0))
    couple = rgy.reger.getAnc(dbing.dgKey(said, bytes(dig))) if dig else None
    if creder is None or couple is None:
        raise MissingCredentialArtifactsError("association issuance is unavailable")
    return creder.issuer, coring.Seqner(qb64b=bytes(couple)).sn


class EndorsementDoer(doing.DoDoer):
    """Keep one outstanding writer nonce and reconstruct all work from LMDB.

    Only verified operations enter this workflow. Recheck KERI/TEL and the EOA
    before publishing; incomplete observations retain current state. Reconcile
    saved transaction identities before same-identity rebroadcast or explicit
    same-nonce cancellation. No in-memory cue authorizes a second nonce.
    """

    def __init__(self, *, baser, rgy, trusted_geda_aid, association_schema,
                 effects, private_key, interval=1.0):
        self.baser, self.rgy, self.effects = baser, rgy, effects
        self.trusted_geda_aid, self.association_schema = trusted_geda_aid, association_schema
        self.account = Account.from_key(private_key)
        if self.account.address != effects.writer:
            raise ValueError("signer does not match the posting writer")
        self.interval, self.cursor = interval, 0
        self.eoa = EoaVerifierDoer(rpc_url=effects.rpc_url)
        baser.bind_posting_scope(PostingScope(
            effects.chain_id, self.account.address.lower(),
            effects.contracts["endorsement"].address.lower(),
            effects.contracts["identity"].address.lower(),
        ))
        super().__init__(doers=[effects, self.eoa, doing.doify(self.run)])

    def desired(self, op):
        """Do not let reordered old issuance overwrite a newer verified result.

        Sequence order exists within one issuer KEL. Conflicting entities or
        associations at one anchor require an operator to resolve the ambiguity;
        lexical SAID order is not an authorization rule.
        """
        positions = []
        for _, candidate in self.baser.ops.getItemIter():
            if candidate.verified.get("wallet") == op.verified["wallet"]:
                issuer, sn = issuance_position(self.rgy, candidate.associationSaid)
                positions.append((issuer, sn, candidate.associationSaid))
        if len({issuer for issuer, _, _ in positions}) != 1:
            raise MissingCredentialArtifactsError("conflicting entity bindings require resolution")
        newest = max(sn for _, sn, _ in positions)
        latest = [said for _, sn, said in positions if sn == newest]
        if len(latest) != 1:
            raise MissingCredentialArtifactsError("ambiguous same-anchor associations require resolution")
        return op.associationSaid == latest[0]

    def current_credential(self, op):
        return verify_association(
            rgy=self.rgy, said=op.associationSaid,
            schema_said=self.association_schema, trusted_geda_aid=self.trusted_geda_aid,
        )

    def evidence(self, op):
        """Yield for profile recheck, then re-read current KERI state."""
        try:
            if not self.desired(op):
                return False, "superseded by newer verified association"
            association = self.current_credential(op)
            self.eoa.msgs.append(association.sad)
            while not self.eoa.cues:
                yield self.tock
            cue = self.eoa.cues.popleft()
            if cue["said"] != op.associationSaid:
                raise RuntimeError("unexpected posting profile result")
            if cue["kin"] == "retry":
                raise MissingCredentialArtifactsError(cue["error"])
            if cue["kin"] == "failed":
                return False, cue["error"]
            self.current_credential(op)
            if cue["verified"]["observation"]["chainId"] != self.effects.chain_id:
                raise MissingCredentialArtifactsError("profile observation is on another chain")
            op.verified = cue["verified"]
            self.baser.pin_operation(op)
            return True, ""
        except (VerificationError, kering.ValidationError) as exc:
            return False, str(exc)

    def sign(self, transaction):
        """Produce an offline signed identity; callers persist before effects."""
        signed = self.account.sign_transaction(transaction)
        return Web3.to_hex(signed.hash), Web3.to_hex(signed.raw_transaction)

    def reconcile(self, intent):
        """Recover submission/receipt progress without allocating another nonce."""
        kin, tx_hash, receipt = yield from self.effects.reconcile(intent)
        op = self.baser.get_operation(intent.associationSaid)
        if kin == "receipt":
            intent.receiptHash = receipt["blockHash"]
            intent.receiptNumber = int(receipt["blockNumber"], 16)
            if receipt["confirmed"]:
                intent.state = "confirmed"
                intent.failureReason = "" if int(receipt["status"], 16) else "transaction reverted"
                if tx_hash not in op.txHashes:
                    op.txHashes.append(tx_hash)
                    self.baser.pin_operation(op)
            self.baser.pin_transaction(intent)
            return
        if intent.receiptHash:
            intent.receiptHash, intent.receiptNumber = "", 0
            self.baser.pin_transaction(intent)
        if kin == "conflict":
            intent.failureReason = "writer nonce consumed without this transaction; operator reconciliation required"
            self.baser.pin_transaction(intent)
            op.failureReason = intent.failureReason
            self.baser.pin_operation(op)
            return
        if intent.cancelHash:
            if kin == "absent":
                yield from self.effects.submit(intent.cancelRaw, intent.cancelHash)
            return
        if intent.action == "withdrawEndorsement":
            if kin == "absent":
                yield from self.effects.submit(intent.rawTransaction, intent.txHash)
            return
        valid, reason = yield from self.evidence(op)
        if not valid and intent.action != "withdrawEndorsement":
            transaction = yield from self.effects.cancellation(intent)
            intent.cancelHash, intent.cancelRaw = self.sign(transaction)
            intent.failureReason = reason
            self.baser.pin_transaction(intent)
            yield from self.effects.submit(intent.cancelRaw, intent.cancelHash)
        elif kin == "absent":
            # There was no yield between this last local check and submission.
            if valid:
                self.current_credential(op)
            yield from self.effects.submit(intent.rawTransaction, intent.txHash)

    def process(self, op):
        """Reconcile one wallet from current evidence and observed chain state."""
        valid, reason = yield from self.evidence(op)
        identity, endorsement = yield from self.effects.observe(op.verified["wallet"])
        action = next_action(op.verified, identity=identity, endorsement=endorsement, valid=valid)
        if action is None:
            state = STATE_ENDORSED if valid else STATE_WITHDRAWN
            if op.state != state or op.failureReason != reason:
                op.state, op.failureReason = state, reason
                self.baser.pin_operation(op)
            return
        transaction = yield from self.effects.prepare(action)
        # Transaction preparation yields. Evidence must still hold afterwards.
        if valid:
            valid, reason = yield from self.evidence(op)
            if not valid:
                return
        tx_hash, raw = self.sign(transaction)
        intent = TransactionRecord(
            txHash=tx_hash, rawTransaction=raw, transaction=transaction,
            associationSaid=op.associationSaid, action=action[1],
        )
        self.baser.pin_transaction(intent)
        # Even first submission uses the same reconciliation path as restart.
        yield from self.reconcile(intent)

    def run(self, tymth, tock=0.0, **opts):
        """Bound work to one transaction or association per polling turn."""
        yield tock
        while True:
            intent = self.baser.unfinished_transaction()
            op = None
            try:
                if intent is not None:
                    yield from self.reconcile(intent)
                else:
                    candidates = [op for _, op in self.baser.ops.getItemIter() if op.verified]
                    if candidates:
                        op = candidates[self.cursor % len(candidates)]
                        self.cursor += 1
                        yield from self.process(op)
            except (OSError, ValueError, KeyError, TypeError, MissingCredentialArtifactsError) as exc:
                # RPC/evidence outages never become a credential-invalid verdict.
                if intent is not None:
                    intent.failureReason = str(exc)
                    self.baser.pin_transaction(intent)
                    failed = self.baser.get_operation(intent.associationSaid)
                    if failed.failureReason != str(exc):
                        failed.failureReason = str(exc)
                        self.baser.pin_operation(failed)
                elif op is not None and op.failureReason != str(exc):
                    op.failureReason = str(exc)
                    self.baser.pin_operation(op)
            deadline = self.tyme + self.interval
            while self.tyme < deadline:
                yield self.tock

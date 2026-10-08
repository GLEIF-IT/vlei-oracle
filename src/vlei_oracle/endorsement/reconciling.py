"""Reconcile durable endorsement work and signed identities on the HIO clock."""

from eth_account import Account
from hio.base import doing
from keri import kering
from keri.core import coring
from keri.db import dbing
from web3 import Web3

from vlei_oracle.verification.credentials import assess_association, reconstruct_binding
from vlei_oracle.basing import STATE_ENDORSED, STATE_WITHDRAWN, PostingScope, TransactionRecord
from vlei_oracle.verification.worker import EoaVerifierDoer
from vlei_oracle.verification.errors import MissingCredentialArtifactsError, VerificationError
from vlei_oracle.endorsement.decisions import ZERO_IDENTITY, next_action


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
        self.interval, self.cursor, self.journal_cursor = interval, 0, 0
        self.eoa = EoaVerifierDoer(rpc_url=effects.rpc_url)
        baser.bind_posting_scope(PostingScope(
            effects.chain_id, self.account.address.lower(),
            effects.contracts["endorsement"].address.lower(),
            effects.contracts["identity"].address.lower(),
        ))
        super().__init__(doers=[effects, self.eoa, doing.doify(self.run)])

    def binding(self, op):
        return reconstruct_binding(
            rgy=self.rgy, said=op.associationSaid,
            schema_said=self.association_schema, grant_said="",
        )

    def desired(self, op):
        """Do not let reordered old issuance overwrite a newer verified result.

        Sequence order exists within one issuer KEL. Conflicting entities or
        associations at one anchor require an operator to resolve the ambiguity;
        lexical SAID order is not an authorization rule.
        """
        positions = []
        for _, candidate in self.baser.managed.getItemIter():
            if self.binding(candidate)["wallet"] == self.binding(op)["wallet"]:
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
        assessment = assess_association(
            rgy=self.rgy, said=op.associationSaid,
            schema_said=self.association_schema, trusted_geda_aid=self.trusted_geda_aid,
        )
        if assessment.outcome == "unavailable":
            raise MissingCredentialArtifactsError(assessment.reason)
        if assessment.outcome == "invalid":
            raise VerificationError(assessment.reason)
        return assessment.credential

    def chain_order(self, op, endorsement):
        """Require evidence for the current assertion before replacing it.

        A fresh operation store may not know a later on-chain association. Read
        immutable issuance anchors from native credential/TEL stores rather
        than treating absence of a local operation as permission to overwrite.
        """
        entity, said = endorsement
        if (entity, said) == (ZERO_IDENTITY, ""):
            return True, ""
        if entity != self.binding(op)["identityRef"]:
            raise MissingCredentialArtifactsError(
                "current wallet entity requires explicit withdrawal before replacement"
            )
        if said == op.associationSaid:
            return True, ""
        try:
            current_issuer, current_sn = issuance_position(self.rgy, said)
        except MissingCredentialArtifactsError as exc:
            raise MissingCredentialArtifactsError(
                "current on-chain association evidence is unavailable"
            ) from exc
        issuer, sn = issuance_position(self.rgy, op.associationSaid)
        if issuer != current_issuer:
            raise MissingCredentialArtifactsError("current endorsement issuer is inconsistent")
        return (True, "") if sn > current_sn else (
            False, "superseded or ambiguous on-chain association"
        )

    def evidence(self, op):
        """Yield for profile recheck, then re-read current KERI state."""
        try:
            association = self.current_credential(op)
            if not self.desired(op):
                return False, "superseded by newer verified association"
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
            if not self.desired(op):
                return False, "superseded by newer verified association"
            if cue["verified"]["observation"]["chainId"] != self.effects.chain_id:
                raise MissingCredentialArtifactsError("profile observation is on another chain")
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
        op = self.baser.get_managed(intent.associationSaid)
        if kin == "receipt":
            if receipt["confirmed"]:
                self.baser.complete_transaction(intent, tx_hash, int(receipt["status"], 16) == 1)
            return
        if kin == "conflict":
            intent.failureReason = "writer nonce consumed without this transaction; operator reconciliation required"
            self.baser.pin_transaction(intent)
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
        if valid:
            _, endorsement = yield from self.effects.observe(self.binding(op)["wallet"])
            try:
                self.current_credential(op)
                valid, reason = (
                    self.chain_order(op, endorsement) if self.desired(op)
                    else (False, "superseded by newer verified association")
                )
            except (VerificationError, kering.ValidationError) as exc:
                valid, reason = False, str(exc)
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

    def refresh_completion(self, intent):
        """Reopen an orphaned attempt from fresh RPC, preserving its identity.

        RPC errors propagate as unknown and never become absence. A previously
        confirmed cancellation also needs this check: a reorg can expose its
        original stale attempt again even when no registry action is needed.
        """
        kin, tx_hash, receipt = yield from self.effects.reconcile(intent)
        if kin == "receipt" and receipt["confirmed"]:
            self.baser.complete_transaction(intent, tx_hash, int(receipt["status"], 16) == 1)
            return True
        intent.state, intent.failureReason = "pending", ""
        self.baser.pin_transaction(intent)
        return False

    def process(self, op):
        """Reconcile one wallet from current evidence and observed chain state."""
        valid, reason = yield from self.evidence(op)
        identity, endorsement = yield from self.effects.observe(self.binding(op)["wallet"])
        if valid:
            valid, reason = self.chain_order(op, endorsement)
        action = next_action(self.binding(op), identity=identity, endorsement=endorsement, valid=valid)
        if action is None:
            state = STATE_ENDORSED if valid else STATE_WITHDRAWN
            if op.state != state or op.failureReason != reason:
                op.state, op.failureReason = state, reason
                self.baser.pin_managed(op)
            return
        # No new nonce while an earlier signed effect has lost confirmation.
        for _, completed in self.baser.transactions.getItemIter():
            if completed.state != "pending" and not (yield from self.refresh_completion(completed)):
                return
        transaction = yield from self.effects.prepare(action)
        # Transaction preparation yields. Evidence must still hold afterwards.
        if valid:
            valid, reason = yield from self.evidence(op)
            if not valid:
                return
        tx_hash, raw = self.sign(transaction)
        intent = TransactionRecord(
            txHash=tx_hash, rawTransaction=raw, nonce=transaction["nonce"],
            gasPrice=transaction["gasPrice"],
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
                    # Bound idle reorg checks to one completed attempt per turn.
                    completed = [record for _, record in self.baser.transactions.getItemIter()
                                 if record.state != "pending"]
                    if completed:
                        intent = completed[self.journal_cursor % len(completed)]
                        self.journal_cursor += 1
                        if not (yield from self.refresh_completion(intent)):
                            continue
                        intent = None
                    candidates = [op for _, op in self.baser.managed.getItemIter()]
                    if candidates:
                        op = candidates[self.cursor % len(candidates)]
                        self.cursor += 1
                        yield from self.process(op)
            except (OSError, ValueError, KeyError, TypeError, MissingCredentialArtifactsError) as exc:
                # RPC/evidence outages never become a credential-invalid verdict.
                if intent is not None:
                    intent.failureReason = str(exc)
                    self.baser.pin_transaction(intent)
                elif op is not None and op.failureReason != str(exc):
                    op.failureReason = str(exc)
                    self.baser.pin_managed(op)
            deadline = self.tyme + self.interval
            while self.tyme < deadline:
                yield self.tock

"""HIO Doers for vLEI Oracle presentation processing.

KERIpy exposes accepted IPEX Grants through its notifier. Association Grants
carry their wallet proof and advance to verification immediately; legacy wallet
attestations first wait for a REST signature. Missing evidence leaves an
operation in ``awaiting_artifacts`` for retry.

Verification-only runs finish at ``verified``. The legacy posting path advances
through ``posting`` to ``posted``; verification or posting errors become
``failed``. A terminal verification result records that completed check, rather
than continuously tracking the credential's later status.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any

from hio.base import doing
from hio.help import decking
from keri.core import coring, serdering
from keri.peer import exchanging

from .association_verifier import verify_association
from .basing import (
    STATE_AWAITING_ARTIFACTS,
    STATE_AWAITING_SIGNATURE,
    STATE_FAILED,
    STATE_POSTED,
    STATE_POSTING,
    STATE_VERIFIED,
    STATE_VERIFYING,
    OperationRecord,
    OracleBaser,
)
from .keri_store import MissingCredentialArtifactsError, presentation_from_reger
from .posting import PostAction, Web3ContractPoster
from .verifier import VerificationError, binding_from_json, verify_presentation


class GrantNotReadyError(Exception):
    """Raised when a grant notification arrives before its EXN material is readable."""


class OracleAgent(doing.DoDoer):
    """Run parser and escrow processors required for direct-mode KERI traffic.

    ``msg_do`` consumes inbound bytes from the Falcon direct-mode endpoint.
    ``escrow_do`` gives KEL, TEL, reply, credential, and EXN escrows time to
    clear without introducing any non-HIO sleep or async model.
    """

    def __init__(self, *, parser, kvy, tvy, rvy, verifier, exc, cues=None, **opts):
        self.parser = parser
        self.kvy = kvy
        self.tvy = tvy
        self.rvy = rvy
        self.verifier = verifier
        self.exc = exc
        self.cues = cues if cues is not None else decking.Deck()
        super().__init__(doers=[doing.doify(self.msg_do), doing.doify(self.escrow_do)], **opts)

    def msg_do(self, tymth=None, tock=0.0, **kwa):
        """Feed queued HTTP direct-mode bytes into the KERI parser."""

        _ = kwa
        self.wind(tymth)
        self.tock = tock
        _ = yield self.tock
        done = yield from self.parser.parsator(local=True)
        return done

    def escrow_do(self, tymth=None, tock=0.25, **kwa):
        """Periodically process KERI, TEL, verifier, and EXN escrows."""

        _ = kwa
        self.wind(tymth)
        self.tock = tock
        _ = yield self.tock
        while True:
            self.kvy.processEscrows()
            self.rvy.processEscrowReply()
            if self.tvy is not None:
                self.tvy.processEscrows()
            if self.verifier is not None:
                self.verifier.processEscrows()
            self.exc.processEscrow()
            yield self.tock


class GrantNotificationDoer(doing.Doer):
    """Convert accepted IPEX Grant notices into durable pending operations.

    KERIpy's grant sender streams attachments before the EXN notice can be
    processed. When the notice is visible before the exchanger can clone all
    material, the Doer leaves the notice in place and retries on a later recur.
    """

    def __init__(
        self,
        *,
        baser: OracleBaser,
        hby,
        notifier,
        parser,
        expected_wallet_schema: str,
        embedded_proof: bool = False,
        **kwa,
    ):
        self.baser = baser
        self.hby = hby
        self.notifier = notifier
        self.parser = parser
        self.expected_wallet_schema = expected_wallet_schema
        self.embedded_proof = embedded_proof
        super().__init__(**kwa)

    def recur(self, tyme):
        """Poll for new grant notices on each HIO scheduler turn."""

        _ = tyme
        self.process_notes()
        return False

    def process_notes(self) -> None:
        """Scan notifier records and consume wallet-attestation grant notices."""

        for keys, notice in list(self.notifier.noter.notes.getItemIter()):
            attrs = notice.attrs
            if attrs.get("r") != "/exn/ipex/grant":
                self.notifier.noter.notes.rem(keys=keys)
                continue

            try:
                self.process_grant_notice(attrs)
            except GrantNotReadyError:
                continue
            except Exception:
                traceback.print_exc()
                self.notifier.noter.notes.rem(keys=keys)
            else:
                self.notifier.noter.notes.rem(keys=keys)

    def process_grant_notice(self, attrs: dict[str, Any]) -> OperationRecord:
        """Parse one IPEX Grant notice and create or fail its operation.

        Embedded ``anc``, ``iss``, and ``acdc`` artifacts are replayed into the
        parser so the verifier store can materialize the wallet credential and
        recursive source chain. The wallet schema check happens early because
        the oracle accepts a pinned schema set, not whatever the sender provides.
        """

        grant_said = attrs["d"]
        exn, pathed = exchanging.cloneMessage(self.hby, said=grant_said)
        if exn is None:
            raise GrantNotReadyError(f"IPEX Grant {grant_said} not found in exchanger store")
        embeds = exn.ked.get("e")
        if not isinstance(embeds, dict) or "acdc" not in embeds:
            raise ValueError(f"IPEX Grant {grant_said} has no embedded ACDC")

        for label in ("anc", "iss", "acdc"):
            if label not in embeds or label not in pathed:
                continue
            sadder = coring.Sadder(ked=embeds[label])
            ims = bytearray(sadder.raw)
            ims.extend(pathed[label])
            self.parser.parseOne(ims=ims)

        acdc = embeds["acdc"]
        wallet_said = acdc["d"]
        wallet_schema = acdc.get("s", "")
        if wallet_schema != self.expected_wallet_schema:
            op = self.baser.start_operation(
                wallet_said=wallet_said,
                grant_said=grant_said,
                grant_sender=acdc.get("i", ""),
                wallet_schema=wallet_schema,
            )
            return self.baser.mark_failed(
                op,
                f"wallet credential schema {wallet_schema} does not match expected {self.expected_wallet_schema}",
            )

        op = self.baser.start_operation(
            wallet_said=wallet_said,
            grant_said=grant_said,
            grant_sender=acdc.get("i", ""),
            wallet_schema=wallet_schema,
        )
        if self.embedded_proof and op.state == STATE_AWAITING_SIGNATURE:
            op.state = STATE_VERIFYING
            self.baser.pin_operation(op)
        return op


class PresentationVerifierDoer(doing.DoDoer):
    """Verify received credentials against accepted KERI state and wallet proof.

    Association Grants carry the proof in the ACDC. Legacy wallet attestations
    wait for the signature endpoint. Missing artifacts remain pending; invalid
    proofs fail. Owns the EOA worker lifecycle and commits its result cues.
    One association is dispatched at a time, bounding in-memory work. RPC errors
    remain retryable; restart rediscovers unfinished operations from the store.
    Verification-only runs terminate at ``verified``.
    """

    def __init__(
        self,
        *,
        baser: OracleBaser,
        rgy,
        trusted_geda_aid: str,
        oracle_registry: str = "",
        verify_saids: bool = True,
        association_schema: str = "",
        eoa_verifier=None,
        retry_delay: float = 5.0,
        no_post: bool = False,
        **kwa,
    ):
        self.baser = baser
        self.rgy = rgy
        self.trusted_geda_aid = trusted_geda_aid
        self.oracle_registry = oracle_registry
        self.verify_saids = verify_saids
        self.association_schema = association_schema
        self.eoa_verifier = eoa_verifier
        self.retry_delay = retry_delay
        # These are scheduling hints only; operation state remains in OracleBaser.
        # Restart may retry sooner, but cannot lose or falsely complete the work.
        self.retry_at = {}
        self.pending = None
        if association_schema and eoa_verifier is None:
            raise ValueError("association verification requires an EOA worker")
        self.no_post = no_post
        children = [eoa_verifier] if eoa_verifier is not None else []
        super().__init__(doers=[*children, doing.doify(self.verify_do)], **kwa)

    def verify_do(self, tymth, tock=0.0, **opts):
        """Coordinate persisted work and worker cues on the shared HIO clock."""
        yield tock
        while True:
            self.process_results()
            self.process_ready()
            yield self.tock

    def process_results(self) -> None:
        """Apply a matching cue; recheck KERI/TEL state after asynchronous work."""
        if self.eoa_verifier is None:
            return
        while self.eoa_verifier.cues:
            cue = self.eoa_verifier.cues.popleft()
            said = cue["said"]
            if said != self.pending:
                continue
            self.pending = None
            op = self.baser.get_operation(said)
            if op is None or op.state != STATE_VERIFYING:
                continue
            if cue["kin"] == "retry":
                op.failureReason = cue["error"]
                op.retryCount += 1
                self.baser.pin_operation(op)
                self.retry_at[said] = self.tyme + self.retry_delay
            elif cue["kin"] == "failed":
                self.baser.mark_failed(op, cue["error"])
            elif cue["kin"] == "verified":
                try:
                    # A revocation can arrive while the RPC client is waiting.
                    verify_association(
                        rgy=self.rgy,
                        said=said,
                        schema_said=self.association_schema,
                        trusted_geda_aid=self.trusted_geda_aid,
                    )
                except MissingCredentialArtifactsError as exc:
                    self.baser.mark_awaiting_artifacts(op, str(exc))
                except VerificationError as exc:
                    self.baser.mark_failed(op, str(exc))
                else:
                    op.verified = cue["verified"]
                    op.state = STATE_VERIFIED
                    op.failureReason = ""
                    self.baser.pin_operation(op)

    def process_ready(self) -> None:
        """Retry operations ready for verification or waiting for KERI artifacts."""

        for op in list(
            self.baser.operations_by_state(STATE_VERIFYING, STATE_AWAITING_ARTIFACTS)
        ):
            if self.association_schema and self.pending is not None:
                break
            if self.retry_at.get(op.walletCredentialSaid, 0) > self.tyme:
                continue
            self.verify_operation(op)

    def verify_operation(self, op: OperationRecord) -> OperationRecord:
        """Verify the selected credential profile before any posting transition."""

        try:
            if self.association_schema:
                # KERIpy may discard an invalid child before logging its bytes.
                # The accepted Grant already owns the immutable candidate; use
                # it for rejection, never as a substitute for KERI acceptance.
                exn = None
                if op.grantSaid:
                    exn, _ = exchanging.cloneMessage(self.rgy.hby, said=op.grantSaid)
                candidate = (
                    serdering.SerderACDC(sad=exn.ked["e"]["acdc"])
                    if exn is not None
                    else None
                )
                association = verify_association(
                    rgy=self.rgy,
                    said=op.walletCredentialSaid,
                    schema_said=self.association_schema,
                    candidate=candidate,
                    trusted_geda_aid=self.trusted_geda_aid,
                )
                # The database remains the work list. The queue contains only the
                # current dispatch, so restart recovers work without queue persistence.
                self.retry_at.pop(op.walletCredentialSaid, None)
                op.state = STATE_VERIFYING
                self.baser.pin_operation(op)
                self.pending = op.walletCredentialSaid
                self.eoa_verifier.msgs.append(association.sad)
                return op
            presentation = presentation_from_reger(
                rgy=self.rgy, said=op.walletCredentialSaid
            )
            binding = verify_presentation(
                presentation,
                trusted_geda_aid=self.trusted_geda_aid,
                verify_saids=self.verify_saids,
                wallet_signature=op.signature,
                wallet_signature_type=op.signatureType,
                chain_id=op.chainId,
                oracle_registry=self.oracle_registry,
            )
            if op.wallet and binding.wallet.lower() != op.wallet.lower():
                raise VerificationError(
                    "signature POST wallet does not match verified wallet attestation"
                )
            if op.chainId and binding.chain_id != op.chainId:
                raise VerificationError(
                    "signature POST chainId does not match verified wallet attestation"
                )
            op.verified = binding.to_json()
            op.state = STATE_VERIFIED if self.no_post else STATE_POSTING
            op.failureReason = ""
            return self.baser.pin_operation(op)
        except MissingCredentialArtifactsError as exc:
            return self.baser.mark_awaiting_artifacts(op, str(exc))
        except Exception as exc:
            return self.baser.mark_failed(op, str(exc))


@dataclass
class PostingSession:
    """In-memory HIO state for one on-chain posting operation."""

    actions: list[PostAction]
    index: int = 0
    pending_tx_hash: str = ""
    tx_hashes: list[str] = field(default_factory=list)


class ContractPosterDoer(doing.Doer):
    """Post verified operations on chain with bounded retries.

    Web3.py performs synchronous RPC calls at the edge, but this Doer owns
    waiting and retry timing. Submitted transaction hashes are polled through
    ``recur`` by setting ``tock`` instead of sleeping inside the Web3 adapter.
    """

    def __init__(
        self,
        *,
        baser: OracleBaser,
        poster: Web3ContractPoster | None,
        initial_aid_allowed: bool = False,
        confirmations: int = 0,
        poll_tock: float = 1.0,
        max_retries: int = 3,
        **kwa,
    ):
        self.baser = baser
        self.poster = poster
        self.initial_aid_allowed = initial_aid_allowed
        self.confirmations = confirmations
        self.poll_tock = poll_tock
        self.max_retries = max_retries
        self.sessions: dict[str, PostingSession] = {}
        super().__init__(**kwa)

    def recur(self, tyme):
        """Advance posting sessions and set the next HIO polling interval."""

        _ = tyme
        self.tock = 0.25
        self.process_ready()
        return False

    def process_ready(self) -> None:
        """Advance every operation that is ready for on-chain posting."""

        for op in list(self.baser.operations_by_state(STATE_POSTING)):
            self.post_operation(op)

    def post_operation(self, op: OperationRecord) -> OperationRecord:
        """Submit or confirm one idempotent posting session for an operation."""

        if self.poster is None:
            return self.baser.mark_failed(op, "contract poster is not configured")
        try:
            session = self.sessions.get(op.walletCredentialSaid)
            if session is None:
                binding = binding_from_json(op.verified)
                session = PostingSession(
                    actions=self.poster.plan_verified_binding(
                        binding,
                        initial_aid_allowed=self.initial_aid_allowed,
                    )
                )
                self.sessions[op.walletCredentialSaid] = session

            if session.pending_tx_hash:
                receipt = self.poster.receipt(session.pending_tx_hash)
                if receipt is None:
                    self.tock = self.poll_tock
                    return op
                if receipt.status != 1:
                    raise RuntimeError(f"transaction failed: {session.pending_tx_hash}")
                if self.confirmations > 0 and self.poster.current_block() < receipt.block_number + self.confirmations:
                    self.tock = self.poll_tock
                    return op
                session.tx_hashes.append(session.pending_tx_hash)
                op.txHashes = sorted(set([*op.txHashes, session.pending_tx_hash]))
                op = self.baser.pin_operation(op)
                session.pending_tx_hash = ""
                session.index += 1

            if session.index < len(session.actions):
                session.pending_tx_hash = self.poster.submit(session.actions[session.index])
                self.tock = self.poll_tock
                return op

            op.txHashes = sorted(set([*op.txHashes, *session.tx_hashes]))
            op.state = STATE_POSTED
            op.failureReason = ""
            self.sessions.pop(op.walletCredentialSaid, None)
            return self.baser.pin_operation(op)
        except Exception as exc:
            self.sessions.pop(op.walletCredentialSaid, None)
            op.retryCount += 1
            if op.retryCount >= self.max_retries:
                return self.baser.mark_failed(op, str(exc))
            op.failureReason = str(exc)
            return self.baser.pin_operation(op)


class RevocationMonitorDoer(doing.Doer):
    """Placeholder for TEL cue based revocation monitoring of posted credentials.

    The current e2e proves initial verification and posting. A production
    follow-up should consume TEL/revocation cues and update posted on-chain
    records when a credential in the accepted chain is revoked.
    """

    def __init__(self, *, baser: OracleBaser, tvy, **kwa):
        self.baser = baser
        self.tvy = tvy
        super().__init__(**kwa)

    def recur(self, tyme):
        """Reserve a HIO slot for future TEL revocation monitoring."""

        _ = tyme
        return False


def pending_states() -> tuple[str, ...]:
    """Return states that represent active, non-terminal operation work."""

    return (STATE_AWAITING_SIGNATURE, STATE_AWAITING_ARTIFACTS, STATE_VERIFYING, STATE_POSTING)


def terminal_states() -> tuple[str, ...]:
    """Return states that no Doer should advance further."""

    return (STATE_VERIFIED, STATE_POSTED, STATE_FAILED)

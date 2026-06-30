"""HIO Doers for vLEI Oracle presentation processing.

The service is intentionally reactive. A standard KERIpy IPEX Grant is parsed
by the KERI stack, the notifier exposes a ``/exn/ipex/grant`` notice, and these
Doers advance a durable operation through the states:

``awaiting_signature -> verifying -> posting -> posted``.

If recursive credentials, TEL events, or KEL events have not arrived yet, the
operation stays non-terminal in ``awaiting_artifacts`` and is retried by HIO.
Only cryptographic, schema, policy, or posting failures mark the operation
``failed``. This keeps the oracle close to Sally's grant-notification model
while replacing Sally's webhook with contract posting.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any

from hio.base import doing
from hio.help import decking
from keri.core import coring
from keri.peer import exchanging

from .basing import (
    OracleBaser,
    OperationRecord,
    STATE_AWAITING_ARTIFACTS,
    STATE_AWAITING_SIGNATURE,
    STATE_FAILED,
    STATE_POSTED,
    STATE_POSTING,
    STATE_VERIFYING,
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

    def __init__(self, *, baser: OracleBaser, hby, notifier, parser, expected_wallet_schema: str, **kwa):
        self.baser = baser
        self.hby = hby
        self.notifier = notifier
        self.parser = parser
        self.expected_wallet_schema = expected_wallet_schema
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

        return self.baser.start_operation(
            wallet_said=wallet_said,
            grant_said=grant_said,
            grant_sender=acdc.get("i", ""),
            wallet_schema=wallet_schema,
        )


class PresentationVerifierDoer(doing.Doer):
    """Verify signed wallet presentation operations once both inputs are present.

    This Doer starts after the IPEX Grant has created a durable operation and
    the signature endpoint has attached the external CESR-decoded Ethereum
    signature. Missing local KERI artifacts are treated as a retry condition;
    failed policy or cryptographic checks are terminal.
    """

    def __init__(
        self,
        *,
        baser: OracleBaser,
        rgy,
        trusted_geda_aid: str,
        oracle_registry: str = "",
        verify_saids: bool = True,
        **kwa,
    ):
        self.baser = baser
        self.rgy = rgy
        self.trusted_geda_aid = trusted_geda_aid
        self.oracle_registry = oracle_registry
        self.verify_saids = verify_saids
        super().__init__(**kwa)

    def recur(self, tyme):
        """Attempt verification for operations ready or waiting on artifacts."""

        _ = tyme
        self.process_ready()
        return False

    def process_ready(self) -> None:
        """Retry operations that have both the Grant path and signature path."""

        for op in list(self.baser.operations_by_state(STATE_VERIFYING, STATE_AWAITING_ARTIFACTS)):
            self.verify_operation(op)

    def verify_operation(self, op: OperationRecord) -> OperationRecord:
        """Load KERI artifacts, verify the GL1 binding, and move to posting."""

        try:
            presentation = presentation_from_reger(rgy=self.rgy, said=op.walletCredentialSaid)
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
                raise VerificationError("signature POST wallet does not match verified wallet attestation")
            if op.chainId and binding.chain_id != op.chainId:
                raise VerificationError("signature POST chainId does not match verified wallet attestation")
            op.verified = binding.to_json()
            op.state = STATE_POSTING
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

    return (STATE_POSTED, STATE_FAILED)

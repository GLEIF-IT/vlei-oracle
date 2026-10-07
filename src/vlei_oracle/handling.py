"""Coordinate accepted IPEX Grants and durable association verification.

The finished ACDC owns its external proof. Operations supply durable scheduling
state; requests and cues are disposable hints. Missing evidence remains pending.
"""

from __future__ import annotations

import traceback
from typing import Any

from hio.base import doing
from hio.help import decking
from keri import kering
from keri.core import coring, serdering
from keri.peer import exchanging

from .association_verifier import verify_association
from .basing import (
    STATE_AWAITING_ARTIFACTS,
    STATE_FAILED,
    STATE_VERIFIED,
    STATE_VERIFYING,
    OperationRecord,
    OracleBaser,
)
from .errors import MissingCredentialArtifactsError, VerificationError


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
        super().__init__(
            doers=[doing.doify(self.msg_do), doing.doify(self.escrow_do)], **opts
        )

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
        expected_association_schema: str,
        **kwa,
    ):
        self.baser = baser
        self.hby = hby
        self.notifier = notifier
        self.parser = parser
        self.expected_association_schema = expected_association_schema
        super().__init__(**kwa)

    def recur(self, tyme):
        """Poll for new grant notices on each HIO scheduler turn."""

        _ = tyme
        self.process_notes()
        return False

    def process_notes(self) -> None:
        """Consume accepted association Grant notices."""

        for keys, notice in list(self.notifier.noter.notes.getItemIter()):
            attrs = notice.attrs
            if attrs.get("r") != "/exn/ipex/grant":
                self.notifier.noter.notes.rem(keys=keys)
                continue

            try:
                self.process_grant_notice(attrs)
            except (GrantNotReadyError, kering.MissingEntryError):
                continue
            except (ValueError, KeyError, TypeError, kering.ValidationError):
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
            raise GrantNotReadyError(
                f"IPEX Grant {grant_said} not found in exchanger store"
            )
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
        association_said = acdc["d"]
        association_schema = acdc.get("s", "")
        if association_schema != self.expected_association_schema:
            op = self.baser.start_operation(
                association_said=association_said,
                grant_said=grant_said,
                grant_sender=exn.ked["i"],
                association_schema=association_schema,
            )
            return self.baser.mark_failed(
                op,
                f"wallet credential schema {association_schema} does not match expected {self.expected_association_schema}",
            )

        op = self.baser.start_operation(
            association_said=association_said,
            grant_said=grant_said,
            grant_sender=exn.ked["i"],
            association_schema=association_schema,
        )
        return op


class PresentationVerifierDoer(doing.DoDoer):
    """Verify received credentials against accepted KERI state and wallet proof.

    Grants carry the proof in the ACDC. Missing artifacts remain pending;
    invalid proofs fail. Owns the EOA worker lifecycle and commits its result cues.
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
        association_schema: str = "",
        eoa_verifier=None,
        retry_delay: float = 5.0,
        **kwa,
    ):
        self.baser = baser
        self.rgy = rgy
        self.trusted_geda_aid = trusted_geda_aid
        self.association_schema = association_schema
        self.eoa_verifier = eoa_verifier
        self.retry_delay = retry_delay
        # These are scheduling hints only; operation state remains in OracleBaser.
        # Restart may retry sooner, but cannot lose or falsely complete the work.
        self.retry_at = {}
        self.pending = None
        if eoa_verifier is None:
            raise ValueError("association verification requires an EOA worker")
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
            if self.pending is not None:
                break
            if self.retry_at.get(op.associationSaid, 0) > self.tyme:
                continue
            self.verify_operation(op)

    def verify_operation(self, op: OperationRecord) -> OperationRecord:
        """Verify the selected credential profile before any posting transition."""

        try:
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
                said=op.associationSaid,
                schema_said=self.association_schema,
                candidate=candidate,
                trusted_geda_aid=self.trusted_geda_aid,
            )
            # The database remains the work list. The queue contains only the
            # current dispatch, so restart recovers work without queue persistence.
            self.retry_at.pop(op.associationSaid, None)
            op.state = STATE_VERIFYING
            self.baser.pin_operation(op)
            self.pending = op.associationSaid
            self.eoa_verifier.msgs.append(association.sad)
            return op
        except MissingCredentialArtifactsError as exc:
            return self.baser.mark_awaiting_artifacts(op, str(exc))
        except (VerificationError, kering.ValidationError) as exc:
            return self.baser.mark_failed(op, str(exc))


def pending_states() -> tuple[str, ...]:
    """States requiring further evidence or verification work."""
    return (STATE_AWAITING_ARTIFACTS, STATE_VERIFYING)


def terminal_states() -> tuple[str, ...]:
    """Completed verification states."""
    return (STATE_VERIFIED, STATE_FAILED)

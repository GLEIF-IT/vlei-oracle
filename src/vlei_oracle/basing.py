"""LMDB-backed state for long-running vLEI Oracle presentation operations.

Operations are keyed by wallet credential SAID and survive process restarts.
The store is intentionally small: it records where the two-step presentation
workflow is, what signature and verified binding were attached, and which
on-chain transactions were submitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from keri.core import coring
from keri.db import dbing, koming
from keri.help import helping


STATE_GRANT_RECEIVED = "grant_received"
STATE_AWAITING_SIGNATURE = "awaiting_signature"
STATE_AWAITING_ARTIFACTS = "awaiting_artifacts"
STATE_VERIFYING = "verifying"
STATE_POSTING = "posting"
STATE_POSTED = "posted"
STATE_FAILED = "failed"
STATE_EXPIRED = "expired"


@dataclass
class OperationRecord:
    """Durable state for one GL1 wallet presentation workflow.

    A record is created by an IPEX Grant notification, advanced by the REST
    signature endpoint, verified by ``PresentationVerifierDoer``, and posted by
    ``ContractPosterDoer``.
    """

    walletCredentialSaid: str
    state: str = STATE_GRANT_RECEIVED
    grantSaid: str = ""
    grantSender: str = ""
    walletSchema: str = ""
    signatureType: str = ""
    signature: str = ""
    wallet: str = ""
    chainId: int = 0
    createdAt: str = ""
    updatedAt: str = ""
    failureReason: str = ""
    retryCount: int = 0
    verified: dict[str, Any] = field(default_factory=dict)
    txHashes: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, *, wallet_said: str, grant_said: str, grant_sender: str, wallet_schema: str) -> "OperationRecord":
        """Create a fresh operation that is waiting for the wallet signature."""

        now = helping.nowIso8601()
        return cls(
            walletCredentialSaid=wallet_said,
            state=STATE_AWAITING_SIGNATURE,
            grantSaid=grant_said,
            grantSender=grant_sender,
            walletSchema=wallet_schema,
            createdAt=now,
            updatedAt=now,
        )

    def touch(self) -> None:
        """Refresh the durable update timestamp before persisting."""

        self.updatedAt = helping.nowIso8601()

    def to_json(self) -> dict[str, Any]:
        """Return the public status shape exposed by the REST endpoint."""

        return {
            "walletCredentialSaid": self.walletCredentialSaid,
            "state": self.state,
            "grantSaid": self.grantSaid,
            "grantSender": self.grantSender,
            "walletSchema": self.walletSchema,
            "signatureType": self.signatureType,
            "wallet": self.wallet,
            "chainId": self.chainId,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            "failureReason": self.failureReason,
            "retryCount": self.retryCount,
            "verified": self.verified,
            "txHashes": self.txHashes,
        }


class OracleBaser(dbing.LMDBer):
    """Local durable operation store for the always-on oracle service."""

    TailDirPath = "vlei-oracle/db"
    AltTailDirPath = ".vlei-oracle/db"
    TempPrefix = "vlei_oracle_db_"

    def __init__(self, name: str = "vlei-oracle", reopen: bool = True, **kwa: Any):
        self.ops: koming.Komer | None = None
        super().__init__(name=name, reopen=reopen, **kwa)

    def reopen(self, **kwa: Any):
        """Open the LMDB environment and bind the operation Komer."""

        super().reopen(**kwa)
        self.ops = koming.Komer(db=self, subkey="ops.", schema=OperationRecord)
        return self.env

    def get_operation(self, wallet_said: str) -> OperationRecord | None:
        """Return the operation keyed by wallet credential SAID, if present."""

        return self.ops.get(keys=(wallet_said,))

    def pin_operation(self, op: OperationRecord) -> OperationRecord:
        """Persist an operation after refreshing its update timestamp."""

        op.touch()
        self.ops.pin(keys=(op.walletCredentialSaid,), val=op)
        return op

    def start_operation(
        self,
        *,
        wallet_said: str,
        grant_said: str,
        grant_sender: str,
        wallet_schema: str,
    ) -> OperationRecord:
        """Create an operation from an IPEX Grant, preserving idempotency."""

        existing = self.get_operation(wallet_said)
        if existing is not None and existing.state != STATE_FAILED:
            return existing
        op = OperationRecord.new(
            wallet_said=wallet_said,
            grant_said=grant_said,
            grant_sender=grant_sender,
            wallet_schema=wallet_schema,
        )
        self.ops.pin(keys=(wallet_said,), val=op)
        return op

    def attach_signature(
        self,
        *,
        wallet_said: str,
        signature_type: str,
        signature: str,
        wallet: str,
        chain_id: int,
    ) -> OperationRecord:
        """Attach the REST-supplied signature and advance to verification."""

        op = self.get_operation(wallet_said)
        if op is None:
            raise KeyError(wallet_said)
        if op.state in (STATE_POSTED, STATE_FAILED, STATE_EXPIRED):
            raise ValueError(f"operation is already {op.state}")
        if op.signature and op.signature != signature:
            raise ValueError("operation already has a different signature")
        op.signatureType = signature_type
        op.signature = signature
        op.wallet = wallet
        op.chainId = chain_id
        if op.state in (STATE_AWAITING_SIGNATURE, STATE_AWAITING_ARTIFACTS):
            op.state = STATE_VERIFYING
        return self.pin_operation(op)

    def mark_awaiting_artifacts(self, op: OperationRecord, reason: str) -> OperationRecord:
        """Record that source-chain artifacts are still arriving."""

        op.state = STATE_AWAITING_ARTIFACTS
        op.failureReason = reason
        return self.pin_operation(op)

    def mark_failed(self, op: OperationRecord, reason: str) -> OperationRecord:
        """Mark an operation terminal after verification or posting failure."""

        op.state = STATE_FAILED
        op.failureReason = reason
        return self.pin_operation(op)

    def operations_by_state(self, *states: str):
        """Yield operations currently in any requested state."""

        wanted = set(states)
        for _, op in self.ops.getItemIter():
            if op.state in wanted:
                yield op


def now_dater() -> coring.Dater:
    """Return a KERI dater for the current wall-clock instant."""

    return coring.Dater(dts=helping.nowIso8601())

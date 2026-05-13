"""LMDB-backed state for long-running vLEI Oracle presentation operations."""

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
    """Durable state for one GL1 wallet presentation workflow."""

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
        self.updatedAt = helping.nowIso8601()

    def to_json(self) -> dict[str, Any]:
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
        super().reopen(**kwa)
        self.ops = koming.Komer(db=self, subkey="ops.", schema=OperationRecord)
        return self.env

    def get_operation(self, wallet_said: str) -> OperationRecord | None:
        return self.ops.get(keys=(wallet_said,))

    def pin_operation(self, op: OperationRecord) -> OperationRecord:
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
        op.state = STATE_AWAITING_ARTIFACTS
        op.failureReason = reason
        return self.pin_operation(op)

    def mark_failed(self, op: OperationRecord, reason: str) -> OperationRecord:
        op.state = STATE_FAILED
        op.failureReason = reason
        return self.pin_operation(op)

    def operations_by_state(self, *states: str):
        wanted = set(states)
        for _, op in self.ops.getItemIter():
            if op.state in wanted:
                yield op


def now_dater() -> coring.Dater:
    return coring.Dater(dts=helping.nowIso8601())

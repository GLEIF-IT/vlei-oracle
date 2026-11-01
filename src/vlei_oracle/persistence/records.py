"""Durable journal, deployment scope and transport replay records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PostingScope:
    """Last observed deployment/writer settings, used only for startup warnings."""

    chainId: int
    writer: str
    endorsement: str
    identity: str


@dataclass
class TransactionRecord:
    """Signed bytes and identity saved before broadcast, with the recorded outcome."""

    txHash: str
    rawTransaction: str
    nonce: int
    associationSaid: str
    functionName: str
    state: str = "pending"


@dataclass
class InboxScope:
    """Last observed producer/chain settings, used only for startup warnings."""

    verifierAid: str
    chainId: int


@dataclass
class PostingOperation:
    """One event's delivery work; membership in a phase table owns its lifecycle.

    attempts counts preparations of the current contract step. rpcFailures bounds
    interrupted pending I/O. signedAt starts the finite transaction-receipt waiting period.
    A persisted txHash fences the writer even when this operation is dead-lettered.
    """

    operationId: str
    eventId: str
    associationSaid: str
    wallet: str
    attempts: int = 0
    rpcFailures: int = 0
    functionName: str = ""
    txHash: str = ""
    signedAt: float = 0.0
    lastTransactionHash: str = ""
    submitted: bool = False
    error: str = ""
    outcome: str = ""


@dataclass
class WalletEffects:
    """Only this writer's confirmed effects, never a refreshed chain-state cache."""

    identityRef: str = ""
    endorsementIdentity: str = ""
    associationSaid: str = ""


@dataclass
class PostingSequence:
    """Durable arrival order and unresolved signed dead-letter fence for one writer."""

    next: int = 0
    blockedBy: str = ""

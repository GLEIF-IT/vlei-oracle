"""Durable journal, deployment scope and transport replay records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PostingScope:
    """Immutable chain/deployment/writer scope of the recovery journal."""

    chainId: int
    writer: str
    endorsement: str
    identity: str


@dataclass
class TransactionRecord:
    """Signed transaction bytes and identity, persisted before any broadcast."""

    txHash: str
    rawTransaction: str
    nonce: int
    associationSaid: str
    functionName: str
    state: str = "pending"


@dataclass
class ReplayClaim:
    """Authenticated transport nonce lifetime, independent of event deduplication."""

    expiresAt: int


@dataclass
class InboxScope:
    """One trusted producer and chain for the durable status history."""

    verifierAid: str
    chainId: int


@dataclass
class PostingOperation:
    """One event's delivery work; membership in a phase table owns its lifecycle.

    attempts counts preparations of the current contract step. rpcFailures bounds
    interrupted pending I/O. signedAt starts the finite receipt waiting period.
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

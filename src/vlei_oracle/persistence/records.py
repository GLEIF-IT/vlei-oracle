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
    """Durable intent and signed identity, written before any broadcast."""

    txHash: str
    rawTransaction: str
    nonce: int
    associationSaid: str
    action: str
    state: str = "pending"
    failureReason: str = ""


@dataclass
class ReplayClaim:
    """Authenticated transport nonce lifetime, independent of event deduplication."""

    expiresAt: int


@dataclass
class InboxScope:
    """One trusted producer and chain for the durable status history."""

    verifierAid: str
    chainId: int

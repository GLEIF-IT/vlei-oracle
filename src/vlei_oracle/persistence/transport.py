"""Authenticated transport replay claims, separate from association nonces."""

from .records import ReplayClaim


def claim_nonce(baser, peer, nonce, expires_at):
    """Insert one transport replay claim without overwriting a consumed key."""
    return baser.replay_claims.put(keys=(peer, nonce), val=ReplayClaim(expires_at))

"""Choose a wallet's association using trusted issuance facts, without network work."""

from dataclasses import dataclass, field


@dataclass
class WalletClaim:
    """Current selection and per-issuer issuance history for one canonical chain/address.

    Retain issuance floors after release and transfer so a delayed predecessor
    cannot become current again. Sequence numbers are comparable only within an AID.
    """

    currentSaid: str = ""
    issuanceByIssuer: dict[str, int] = field(default_factory=dict)


def update_selection(status, *, current, claim):
    """Update these borrowed records in memory; the caller commits them together.

    status is a newer authenticated verifier status with immutable issuance facts.
    Its previous selection decision is preserved by the caller. ACKs acknowledge
    receipt even when ownership policy rejects the claim; no contract effect occurs.
    """
    issuer = status.association["issuerAid"]
    sequence = status.association["issuance"]["sequenceNumber"]
    previous_sequence = claim.issuanceByIssuer.get(issuer, -1)
    claim.issuanceByIssuer[issuer] = max(previous_sequence, sequence)

    if current is not None and current.associationSaid == status.associationSaid:
        status.selection = "revoked" if status.status == "revoked" else "selected"
        if status.status == "revoked":
            claim.currentSaid = ""
        return

    # A known newer issuance supersedes the same LE's predecessor even if its
    # retirement arrived before its acceptance event. Never compare different KELs.
    if (
        current is not None
        and current.association["issuerAid"] == issuer
        and sequence > current.association["issuance"]["sequenceNumber"]
    ):
        current.selection = "superseded"
        claim.currentSaid = ""
        current = None

    if status.status == "revoked":
        status.selection = "revoked"
    elif status.selection in ("rejected", "superseded"):
        return  # A later revision/duplicate cannot promote an earlier rejected claim.
    elif sequence <= previous_sequence:
        status.selection = "superseded"
    elif current is not None:
        status.selection = "rejected"  # Another LE still owns the active claim.
    else:
        status.selection = "selected"
        claim.currentSaid = status.associationSaid

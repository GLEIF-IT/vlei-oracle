"""Pure wallet selection over borrowed domain records."""


def update_selection(status, *, current, claim):
    """Update these borrowed records in memory; the caller commits them together.

    status is a newer authenticated verifier status with immutable issuance facts.
    Its previous selection decision is preserved by the caller. ACKs acknowledge
    receipt even when ownership policy rejects the claim; no contract effect occurs.
    """
    issuer = status.association["issuerAid"]
    sequence = status.association["issuanceAnchor"]["sequenceNumber"]
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
        and sequence > current.association["issuanceAnchor"]["sequenceNumber"]
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

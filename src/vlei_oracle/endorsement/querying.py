"""Association-scoped projection of locally recorded endorsement progress."""


class EndorsementQuery:
    """Return one association's result without exposing posting bookkeeping."""

    def __init__(self, reader):
        self.reader = reader

    def endorsement(self, said):
        """Return publication state and latest request progress, or None if unknown.

        Publication is attributed only to this exact SAID. Wallet-level records
        supply evidence internally; neither their contents nor global posting
        state are returned. This is local knowledge, not a fresh Ethereum query.
        """
        associationStatus = self.reader.association(said)
        if associationStatus is None:
            return None
        phase, postingOperation = self.reader.operation_for(
            f"{said}:{associationStatus.statusRevision}"
        )
        walletEffects = self.reader.wallet_effects(associationStatus.wallet)
        published = walletEffects is not None and walletEffects.associationSaid == said
        action = "withdraw" if associationStatus.status == "revoked" else "publish"
        if phase == "dead":
            deliveryStatus = "failed"
        elif phase == "queued":
            deliveryStatus = "queued"
        elif phase in ("preparing", "pending"):
            deliveryStatus = "in_progress"
        elif action == "publish" and associationStatus.selection in (
            "superseded",
            "rejected",
        ):
            deliveryStatus = associationStatus.selection
        elif phase == "completed":
            deliveryStatus = "completed"
        else:
            raise ValueError("association has no publication or withdrawal request")
        return {
            "associationSaid": said,
            "wallet": associationStatus.wallet,
            "publicationStatus": "published" if published else "not_published",
            "action": action,
            "deliveryStatus": deliveryStatus,
            "error": postingOperation.error if postingOperation else "",
        }

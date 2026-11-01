"""Trusted status and wallet-selection domain records."""

from dataclasses import dataclass, field


@dataclass
class StatusEvent:
    """Immutable authenticated event bytes, retained for duplicate/conflict detection."""

    eventId: str
    body: str
    operationId: str = ""


@dataclass
class AssociationStatus:
    """Latest verifier status and immutable facts, with a separate local selection decision."""

    associationSaid: str
    statusRevision: int
    status: str
    association: dict
    wallet: str
    identityRef: str
    selection: str = (
        "unselected"  # selected, superseded, rejected or revoked after selection.
    )


@dataclass
class WalletClaim:
    """Current selection and per-issuer issuance history for one canonical chain/address.

    Retain issuance floors after release and transfer so a delayed predecessor
    cannot become current again. Sequence numbers are comparable only within an AID.
    """

    currentSaid: str = ""
    issuanceByIssuer: dict[str, int] = field(default_factory=dict)

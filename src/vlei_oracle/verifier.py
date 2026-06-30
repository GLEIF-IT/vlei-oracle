"""GL1 vLEI chain and wallet attestation verification.

The verifier reduces a local KERI credential store plus one external wallet
signature into a compact ``VerifiedWalletBinding`` suitable for on-chain
posting. It expects the credential chain:

GEDA -> QVI -> Legal Entity -> OOR Auth -> OOR -> GL1 Wallet Attestation.

The wallet ACDC is self-attested by the OOR holder, so its LE and OOR edges must
use ``NI2I``. The Ethereum signature is supplied out of band by the presentation
workflow and is verified against EIP-712 typed data rebuilt from the wallet
ACDC fields plus the POST/configured EIP-712 domain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .constants import (
    LE_SCHEMA,
    OOR_AUTH_SCHEMA,
    OOR_SCHEMA,
    QVI_SCHEMA,
    WALLET_EDGE_OPERATOR,
    WALLET_SCHEMA,
    WALLET_SIGNATURE_TYPE,
)
from .keri_acdc import verify_acdc_raw, verify_said
from .wallet import build_typed_data, verify_typed_signature


class VerificationError(Exception):
    """Raised when a presentation fails GL1 vLEI verification."""


@dataclass(frozen=True)
class VerifiedWalletBinding:
    """Compact verified evidence that can be posted on chain.

    This is the stable boundary between KERI/ACDC verification and contract
    posting. It intentionally contains compact identifiers rather than raw
    credential bodies.
    """

    legal_entity_aid: str
    lei: str
    wallet: str
    chain_id: int
    le_credential_said: str
    oor_credential_said: str
    wallet_credential_said: str
    wallet_schema: str
    nonce: str
    expires_at: int

    def to_json(self) -> dict[str, Any]:
        """Return the durable JSON representation stored on an operation."""

        return {
            "legalEntityAid": self.legal_entity_aid,
            "lei": self.lei,
            "wallet": self.wallet,
            "chainId": self.chain_id,
            "leCredentialSaid": self.le_credential_said,
            "oorCredentialSaid": self.oor_credential_said,
            "walletCredentialSaid": self.wallet_credential_said,
            "walletSchema": self.wallet_schema,
            "nonce": self.nonce,
            "expiresAt": self.expires_at,
        }


def binding_from_json(data: dict[str, Any]) -> VerifiedWalletBinding:
    """Build a verified binding object from its durable JSON representation."""

    return VerifiedWalletBinding(
        legal_entity_aid=data["legalEntityAid"],
        lei=data["lei"],
        wallet=data["wallet"],
        chain_id=int(data["chainId"]),
        le_credential_said=data["leCredentialSaid"],
        oor_credential_said=data["oorCredentialSaid"],
        wallet_credential_said=data["walletCredentialSaid"],
        wallet_schema=data["walletSchema"],
        nonce=data["nonce"],
        expires_at=int(data["expiresAt"]),
    )


def verify_presentation(
    presentation: dict[str, Any],
    *,
    trusted_geda_aid: str,
    verify_saids: bool = True,
    now: int | None = None,
    wallet_signature: str | None = None,
    wallet_signature_type: str | None = None,
    chain_id: int,
    oracle_registry: str,
) -> VerifiedWalletBinding:
    """Verify a GL1 wallet attestation presentation.

    The presentation is intentionally explicit: credentials are keyed by SAID
    and include their ACDC body plus optional TEL-derived status records.
    ``wallet_signature`` is the REST-supplied Ethereum signature after CESR
    decoding; it must not be embedded in the wallet credential itself.
    """

    credentials = presentation.get("credentials")
    if not isinstance(credentials, dict):
        raise VerificationError("presentation.credentials must be an object keyed by SAID")
    credential_raws = presentation.get("credentialRaws", {})
    if not isinstance(credential_raws, dict):
        credential_raws = {}

    wallet_said = presentation.get("walletCredential")
    if not isinstance(wallet_said, str) or not wallet_said:
        raise VerificationError("presentation.walletCredential is required")

    revoked = set(presentation.get("revokedCredentials", []))
    statuses = presentation.get("credentialStatuses", {})
    if isinstance(statuses, dict):
        revoked.update(
            said
            for said, status in statuses.items()
            if isinstance(status, dict) and status.get("et") in ("rev", "brv")
        )
    current_time = int(time.time()) if now is None else now

    wallet_cred = _credential(
        credentials, wallet_said, WALLET_SCHEMA, revoked, credential_raws=credential_raws, verify_saids=verify_saids
    )
    wallet_attrs = _attrs(wallet_cred)
    wallet_edges = _edges(wallet_cred)

    le_said = _edge_said(wallet_edges, "le", expected_schema=LE_SCHEMA, expected_operator=WALLET_EDGE_OPERATOR)
    oor_said = _edge_said(wallet_edges, "oor", expected_schema=OOR_SCHEMA, expected_operator=WALLET_EDGE_OPERATOR)

    le_cred = _credential(
        credentials, le_said, LE_SCHEMA, revoked, credential_raws=credential_raws, verify_saids=verify_saids
    )
    oor_cred = _credential(
        credentials, oor_said, OOR_SCHEMA, revoked, credential_raws=credential_raws, verify_saids=verify_saids
    )
    oor_attrs = _attrs(oor_cred)
    oor_edges = _edges(oor_cred)

    auth_said = _edge_said(oor_edges, "auth", expected_schema=OOR_AUTH_SCHEMA)
    auth_cred = _credential(
        credentials, auth_said, OOR_AUTH_SCHEMA, revoked, credential_raws=credential_raws, verify_saids=verify_saids
    )
    auth_attrs = _attrs(auth_cred)
    auth_edges = _edges(auth_cred)

    le_from_auth_said = _edge_said(auth_edges, "le", expected_schema=LE_SCHEMA)
    if le_from_auth_said != le_said:
        raise VerificationError("OOR Auth edge does not reference wallet LE credential")

    qvi_said = _edge_said(_edges(le_cred), "qvi", expected_schema=QVI_SCHEMA)
    qvi_cred = _credential(
        credentials, qvi_said, QVI_SCHEMA, revoked, credential_raws=credential_raws, verify_saids=verify_saids
    )
    if qvi_cred.get("i") != trusted_geda_aid:
        raise VerificationError("QVI credential issuer is not the trusted GEDA AID")

    le_attrs = _attrs(le_cred)
    legal_entity_aid = _required_str(le_attrs, "i")
    lei = _required_str(le_attrs, "LEI")
    oor_aid = _required_str(oor_attrs, "i")

    if _required_str(wallet_attrs, "legalEntityAid") != legal_entity_aid:
        raise VerificationError("wallet attestation legalEntityAid does not match LE credential")
    if isinstance(wallet_attrs.get("i"), str) and wallet_attrs["i"] != legal_entity_aid:
        raise VerificationError("wallet attestation subject AID does not match LE credential")
    if wallet_cred.get("i") != oor_aid:
        raise VerificationError("wallet attestation issuer must be the OOR holder AID")

    _check_lei_consistency(lei, wallet_attrs, oor_attrs, auth_attrs)
    _check_oor_auth_matches_oor(auth_attrs, oor_attrs)
    expires_at = _required_int(wallet_attrs, "expiresAt")
    if expires_at <= current_time:
        raise VerificationError("wallet attestation is expired")

    typed_data = build_typed_data(
        legal_entity_aid=legal_entity_aid,
        lei=lei,
        wallet=_required_str(wallet_attrs, "wallet"),
        chain_id=chain_id,
        oracle_registry=oracle_registry,
        nonce=_required_str(wallet_attrs, "nonce"),
        expires_at=expires_at,
    )
    signature = _resolve_wallet_signature(
        wallet_attrs,
        wallet_signature=wallet_signature,
        wallet_signature_type=wallet_signature_type,
    )
    wallet = _required_str(wallet_attrs, "wallet")
    if not verify_typed_signature(typed_data=typed_data, wallet=wallet, signature=signature):
        raise VerificationError("wallet signature did not verify against GL1 typed data")

    return VerifiedWalletBinding(
        legal_entity_aid=legal_entity_aid,
        lei=lei,
        wallet=wallet,
        chain_id=chain_id,
        le_credential_said=le_said,
        oor_credential_said=oor_said,
        wallet_credential_said=wallet_said,
        wallet_schema=WALLET_SCHEMA,
        nonce=_required_str(wallet_attrs, "nonce"),
        expires_at=expires_at,
    )


def _resolve_wallet_signature(
    wallet_attrs: dict[str, Any],
    *,
    wallet_signature: str | None,
    wallet_signature_type: str | None,
) -> str:
    """Require an external signature and reject embedded signature fields."""

    if wallet_signature is None:
        raise VerificationError("external wallet signature is required for wallet attestation")

    if wallet_signature_type != WALLET_SIGNATURE_TYPE:
        raise VerificationError("wallet signature type is not supported")
    if "walletSignature" in wallet_attrs or "walletSignatureType" in wallet_attrs:
        raise VerificationError("wallet attestation must not embed wallet signature fields")
    return wallet_signature


def _credential(
    credentials: dict[str, Any],
    said: str,
    schema: str,
    revoked: set[str],
    *,
    credential_raws: dict[str, Any],
    verify_saids: bool,
) -> dict[str, Any]:
    """Fetch and validate one credential by SAID, schema, status, and SAID."""

    if said in revoked:
        raise VerificationError(f"credential {said} is revoked")
    cred = credentials.get(said)
    if not isinstance(cred, dict):
        raise VerificationError(f"credential {said} is missing")
    if cred.get("d") != said:
        raise VerificationError(f"credential {said} has mismatched d field")
    if cred.get("s") != schema:
        raise VerificationError(f"credential {said} has schema {cred.get('s')}, expected {schema}")
    if verify_saids:
        raw = credential_raws.get(said)
        said_verified = verify_acdc_raw(raw, said=said) if isinstance(raw, str) else verify_said(cred)
        if not said_verified:
            raise VerificationError(f"credential {said} SAID does not verify")
    if cred.get("status") in ("revoked", "expired", "rev", "brv"):
        raise VerificationError(f"credential {said} status is {cred.get('status')}")
    return cred


def _attrs(credential: dict[str, Any]) -> dict[str, Any]:
    attrs = credential.get("a")
    if not isinstance(attrs, dict):
        raise VerificationError(f"credential {credential.get('d')} has no embedded attributes")
    return attrs


def _edges(credential: dict[str, Any]) -> dict[str, Any]:
    edges = credential.get("e")
    if not isinstance(edges, dict):
        raise VerificationError(f"credential {credential.get('d')} has no embedded edges")
    return edges


def _edge_said(
    edges: dict[str, Any],
    key: str,
    *,
    expected_schema: str,
    expected_operator: str | None = None,
) -> str:
    """Return an edge target SAID after schema and operator validation."""

    node = edges.get(key)
    if not isinstance(node, dict):
        raise VerificationError(f"missing edge {key}")
    if node.get("s") != expected_schema:
        raise VerificationError(f"edge {key} has schema {node.get('s')}, expected {expected_schema}")
    if expected_operator is not None and node.get("o") != expected_operator:
        raise VerificationError(f"edge {key} has operator {node.get('o')}, expected {expected_operator}")
    return _required_str(node, "n")


def _required_str(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise VerificationError(f"missing string field {key}")
    return value


def _required_int(obj: dict[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool):
        raise VerificationError(f"field {key} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise VerificationError(f"missing integer field {key}")


def _check_lei_consistency(
    lei: str,
    wallet_attrs: dict[str, Any],
    oor_attrs: dict[str, Any],
    auth_attrs: dict[str, Any],
) -> None:
    """Require the same LEI across LE, OOR Auth, OOR, and wallet evidence."""

    for label, attrs in (("wallet", wallet_attrs), ("OOR", oor_attrs), ("OOR Auth", auth_attrs)):
        if _required_str(attrs, "LEI") != lei:
            raise VerificationError(f"{label} LEI does not match LE credential")


def _check_oor_auth_matches_oor(auth_attrs: dict[str, Any], oor_attrs: dict[str, Any]) -> None:
    """Require OOR Auth subject details to match the issued OOR credential."""

    comparisons = [
        ("AID", "i", "OOR holder AID"),
        ("personLegalName", "personLegalName", "person legal name"),
        ("officialRole", "officialRole", "official role"),
    ]
    for auth_key, oor_key, label in comparisons:
        if _required_str(auth_attrs, auth_key) != _required_str(oor_attrs, oor_key):
            raise VerificationError(f"OOR Auth {label} does not match OOR credential")

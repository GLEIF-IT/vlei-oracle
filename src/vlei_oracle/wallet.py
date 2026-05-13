"""Ethereum wallet binding challenge and verification helpers."""

from __future__ import annotations

import json
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from .constants import EIP712_DOMAIN_NAME, EIP712_DOMAIN_VERSION, EIP712_PRIMARY_TYPE, WALLET_SIGNATURE_TYPE


def _checksum_passthrough(address: str) -> str:
    if not isinstance(address, str) or not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"invalid EVM address: {address!r}")
    return address


def canonical_json(data: dict[str, Any]) -> str:
    """Return deterministic JSON for files passed to cast."""

    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def build_typed_data(
    *,
    legal_entity_aid: str,
    lei: str,
    oor_aid: str,
    le_credential_said: str,
    oor_credential_said: str,
    wallet: str,
    chain_id: int,
    vlei_token: str,
    wrapper: str,
    identity_registry: str,
    credential_registry: str,
    oracle_registry: str,
    nonce: str,
    expires_at: int,
    purpose: str,
    issued_at: int = 0,
) -> dict[str, Any]:
    """Build the EIP-712 typed data signed by the Ethereum wallet."""

    if chain_id <= 0:
        raise ValueError("chain_id must be positive")
    if expires_at <= 0:
        raise ValueError("expires_at must be a unix timestamp")

    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            EIP712_PRIMARY_TYPE: [
                {"name": "legalEntityAid", "type": "string"},
                {"name": "lei", "type": "string"},
                {"name": "oorAid", "type": "string"},
                {"name": "leCredentialSaid", "type": "string"},
                {"name": "oorCredentialSaid", "type": "string"},
                {"name": "wallet", "type": "address"},
                {"name": "chainId", "type": "uint256"},
                {"name": "vleiToken", "type": "address"},
                {"name": "wrapper", "type": "address"},
                {"name": "identityRegistry", "type": "address"},
                {"name": "credentialRegistry", "type": "address"},
                {"name": "oracleRegistry", "type": "address"},
                {"name": "nonce", "type": "string"},
                {"name": "issuedAt", "type": "uint256"},
                {"name": "expiresAt", "type": "uint256"},
                {"name": "purpose", "type": "string"},
            ],
        },
        "primaryType": EIP712_PRIMARY_TYPE,
        "domain": {
            "name": EIP712_DOMAIN_NAME,
            "version": EIP712_DOMAIN_VERSION,
            "chainId": chain_id,
            "verifyingContract": _checksum_passthrough(oracle_registry),
        },
        "message": {
            "legalEntityAid": legal_entity_aid,
            "lei": lei,
            "oorAid": oor_aid,
            "leCredentialSaid": le_credential_said,
            "oorCredentialSaid": oor_credential_said,
            "wallet": _checksum_passthrough(wallet),
            "chainId": chain_id,
            "vleiToken": _checksum_passthrough(vlei_token),
            "wrapper": _checksum_passthrough(wrapper),
            "identityRegistry": _checksum_passthrough(identity_registry),
            "credentialRegistry": _checksum_passthrough(credential_registry),
            "oracleRegistry": _checksum_passthrough(oracle_registry),
            "nonce": nonce,
            "issuedAt": issued_at,
            "expiresAt": expires_at,
            "purpose": purpose,
        },
    }


def verify_typed_signature(
    *,
    typed_data: dict[str, Any],
    wallet: str,
    signature: str,
    cast_bin: str | None = None,
) -> bool:
    """Verify an EIP-712 signature by recovering the signer in Python."""

    _ = cast_bin
    try:
        signable = encode_typed_data(full_message=typed_data)
        recovered = Account.recover_message(signable, signature=signature)
    except Exception:
        return False

    return recovered.lower() == wallet.lower()


def wallet_attributes_from_typed_data(
    *,
    typed_data: dict[str, Any],
    signature: str,
) -> dict[str, Any]:
    """Return GL1 Wallet Attestation ACDC attributes from typed data."""

    message = dict(typed_data["message"])
    return {
        "legalEntityAid": message["legalEntityAid"],
        "LEI": message["lei"],
        "oorAid": message["oorAid"],
        "wallet": message["wallet"],
        "chainId": message["chainId"],
        "vleiToken": message["vleiToken"],
        "wrapper": message["wrapper"],
        "identityRegistry": message["identityRegistry"],
        "credentialRegistry": message["credentialRegistry"],
        "oracleRegistry": message["oracleRegistry"],
        "walletSignatureType": WALLET_SIGNATURE_TYPE,
        "walletSignature": signature,
        "nonce": message["nonce"],
        "issuedAt": message["issuedAt"],
        "expiresAt": message["expiresAt"],
        "purpose": message["purpose"],
    }

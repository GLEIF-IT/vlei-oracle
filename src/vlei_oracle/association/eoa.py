"""EOA wire identifiers and issuer-side wallet challenge/proof checks."""

from __future__ import annotations

import re
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys.constants import SECPK1_N
from eth_keys.exceptions import BadSignature
from eth_utils import is_checksum_address, keccak
from keri.core import coring

DOMAIN_TAG = "vlei-external-association/v1"

PROOF_ALGORITHM = "EIP-712/ES256K"
PRIMARY_TYPE = "ExternalAssociation"


def validate_nonce(nonce: str) -> None:
    """Accept 1–256 characters without changing the nonce's representation."""
    if not isinstance(nonce, str) or not 1 <= len(nonce) <= 256:
        raise ValueError("nonce must be a string of 1 to 256 characters")


def validate_association_identifiers(
    *, issuer_aid: str, le_vlei_said: str, nonce: str
) -> None:
    """Validate KERI issuer and credential identifiers and the nonce length."""
    validate_nonce(nonce)
    if coring.Prefixer(qb64=issuer_aid).qb64 != issuer_aid:
        raise ValueError("issuer AID must be canonical with no trailing material")
    if coring.Saider(qb64=le_vlei_said).qb64 != le_vlei_said:
        raise ValueError(
            "LE credential SAID must be canonical with no trailing material"
        )


def normalize_ext_id(chain_id: int, address: str) -> str:
    """Return lowercase CAIP-10, checking EIP-55 on mixed-case input addresses."""
    if (
        type(chain_id) is not int
        or not 0 < chain_id < 2**256
        or len(str(chain_id)) > 32
    ):
        raise ValueError(
            "chain ID must be a positive uint256 within the CAIP-2 32-character reference limit"
        )
    if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        raise ValueError("account must be a 20-byte 0x-prefixed address")
    letters = address[2:]
    if not (
        letters == letters.lower() or letters == letters.upper()
    ) and not is_checksum_address(address):
        raise ValueError("mixed-case account has an invalid EIP-55 checksum")
    return f"eip155:{chain_id}:{address.lower()}"


def ext_id_chain(ext_id: str) -> int:
    """Return the chain ID from a canonical EOA CAIP-10 identifier.

    Committed identifiers must already be normalized; aliases are rejected.
    """
    if not isinstance(ext_id, str) or not re.fullmatch(
        r"eip155:[1-9][0-9]{0,31}:0x[0-9a-f]{40}", ext_id
    ):
        raise ValueError("external identifier must be canonical EOA CAIP-10")
    chain_id = int(ext_id.split(":")[1])
    if chain_id >= 2**256:
        raise ValueError("chain ID exceeds uint256")
    return chain_id


def build_challenge(
    *, issuer_aid: str, le_vlei_said: str, ext_id: str, nonce: str
) -> dict[str, Any]:
    """Bind only committed association fields and fixed profile constants.

    There is no verifyingContract: this is an off-chain association, independent
    of any endorsement deployment. Chain scope is derived from extId.
    """
    chain_id = ext_id_chain(ext_id)
    validate_association_identifiers(
        issuer_aid=issuer_aid, le_vlei_said=le_vlei_said, nonce=nonce
    )
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            PRIMARY_TYPE: [
                {"name": "issuerAid", "type": "string"},
                {"name": "leVleiSaid", "type": "string"},
                {"name": "extId", "type": "string"},
                {"name": "nonce", "type": "string"},
            ],
        },
        "primaryType": PRIMARY_TYPE,
        # Decimal text preserves large chain IDs through JSON clients; EIP-712
        # still encodes this field as uint256 according to the domain type.
        "domain": {"name": DOMAIN_TAG, "version": "1", "chainId": str(chain_id)},
        "message": {
            "issuerAid": issuer_aid,
            "leVleiSaid": le_vlei_said,
            "extId": ext_id,
            "nonce": nonce,
        },
    }


def challenge_hashes(challenge: dict[str, Any]) -> dict[str, str]:
    """Return EIP-712 domain, message, and signing digests using eth-account."""
    signable = encode_typed_data(full_message=challenge)
    # EIP-712 signs keccak256(0x1901 || domainSeparator || hashStruct(message)).
    return {
        "domainSeparator": "0x" + signable.header.hex(),
        "messageHash": "0x" + signable.body.hex(),
        "signingDigest": "0x"
        + keccak(b"\x19" + signable.version + signable.header + signable.body).hex(),
    }


def verify_eoa_proof(association: dict[str, Any]) -> None:
    """Check the committed EIP-712 proof locally, without network access."""
    attrs = association["a"]
    proof = attrs["proof"]
    if proof["alg"] != PROOF_ALGORITHM or proof["tag"] != DOMAIN_TAG:
        raise ValueError("unsupported EOA proof algorithm or domain")
    signature = proof["sig"]
    if not re.fullmatch(r"0x[0-9a-f]{128}(1b|1c)", signature):
        raise ValueError("invalid EOA signature encoding")
    raw = bytes.fromhex(signature[2:])
    r, s = int.from_bytes(raw[:32]), int.from_bytes(raw[32:64])
    if not 1 <= r < SECPK1_N or not 1 <= s <= SECPK1_N // 2:
        raise ValueError("EOA signature must have valid r and low-s scalars")
    challenge = build_challenge(
        issuer_aid=association["i"],
        le_vlei_said=association["e"]["le"]["n"],
        ext_id=attrs["extId"],
        nonce=association["u"],
    )
    wallet = attrs["extId"].split(":")[2]
    try:
        signer = Account.recover_message(
            encode_typed_data(full_message=challenge), signature=signature
        )
    except BadSignature as exc:
        raise ValueError("EOA proof signature cannot recover a signer") from exc
    if signer.lower() != wallet:
        raise ValueError("EOA proof signer does not match external identifier")

"""On-chain posting helpers for verified vLEI oracle evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cast import keccak, run_cast
from .constants import WALLET_CREDENTIAL_TYPE
from .verifier import VerifiedWalletBinding


def parse_deploy_log(text: str) -> dict[str, str]:
    """Parse DeployAll console output into contract addresses."""

    labels = {
        "AdminControls": "adminControls",
        "Vendor IdentityRegistry": "identityRegistry",
        "CredentialRegistry": "credentialRegistry",
        "VLEIOracleRegistry": "oracleRegistry",
        "Asset (vLEI)": "vleiToken",
        "Wrapper (wvLEI)": "wrapper",
    }
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for label, key in labels.items():
            prefix = f"{label}:"
            if line.startswith(prefix):
                result[key] = line.split(prefix, 1)[1].strip()

    missing = sorted(set(labels.values()) - set(result))
    if missing:
        raise ValueError(f"deployment log missing addresses: {', '.join(missing)}")
    return result


def load_json_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def wallet_credential_type_id(binding: VerifiedWalletBinding, *, cast_bin: str | None = None) -> str:
    material = f"{WALLET_CREDENTIAL_TYPE}:{binding.chain_id}:{binding.wallet.lower()}"
    return keccak(material, cast_bin=cast_bin)


def proof_hash(binding: VerifiedWalletBinding, *, cast_bin: str | None = None) -> str:
    material = (
        f"{binding.wallet_credential_said}:"
        f"{binding.le_credential_said}:"
        f"{binding.oor_credential_said}:"
        f"{binding.wallet.lower()}:"
        f"{binding.nonce}"
    )
    return keccak(material, cast_bin=cast_bin)


def post_verified_binding(
    binding: VerifiedWalletBinding,
    *,
    contracts: dict[str, str],
    rpc_url: str,
    private_key: str,
    cast_bin: str | None = None,
    initial_aid_allowed: bool = False,
) -> None:
    """Post verified identity, credential, and Legal Entity evidence on chain."""

    required = ["identityRegistry", "credentialRegistry", "oracleRegistry"]
    missing = [key for key in required if key not in contracts]
    if missing:
        raise ValueError(f"contracts missing keys: {', '.join(missing)}")

    aid_ccid = keccak(binding.legal_entity_aid, cast_bin=cast_bin)
    credential_type_id = wallet_credential_type_id(binding, cast_bin=cast_bin)
    proof = proof_hash(binding, cast_bin=cast_bin)
    le_said_hash = keccak(binding.le_credential_said, cast_bin=cast_bin)

    common = ["--rpc-url", rpc_url, "--private-key", private_key]

    run_cast(
        [
            "send",
            contracts["identityRegistry"],
            "registerIdentity(bytes32,address,bytes)",
            aid_ccid,
            binding.wallet,
            "0x",
            *common,
        ],
        cast_bin=cast_bin,
    )
    run_cast(
        [
            "send",
            contracts["credentialRegistry"],
            "registerCredential(bytes32,bytes32,uint40,bytes,bytes)",
            aid_ccid,
            credential_type_id,
            str(binding.expires_at),
            proof,
            "0x",
            *common,
        ],
        cast_bin=cast_bin,
    )
    run_cast(
        [
            "send",
            contracts["oracleRegistry"],
            "postLegalEntityRecord(string,string,string,bytes32,bytes32,uint40,uint40,bool,bool)",
            binding.legal_entity_aid,
            binding.lei,
            binding.lei,
            le_said_hash,
            proof,
            str(binding.issued_at),
            str(binding.expires_at),
            "true" if initial_aid_allowed else "false",
            "false",
            *common,
        ],
        cast_bin=cast_bin,
    )


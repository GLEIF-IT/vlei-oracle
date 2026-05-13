"""Client-side helpers for the wallet-signature REST step.

The shell e2e uses standard KERIpy CLI commands for IPEX Grant delivery. This
module owns only the second leg of the presentation workflow: build or accept a
CESR-encoded Ethereum signature and POST it to the oracle operation created by
the Grant notification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from eth_account import Account
from eth_account.messages import encode_typed_data

from .constants import WALLET_SIGNATURE_TYPE
from .wallet import encode_eth_signature_cesr


@dataclass
class HttpResult:
    """Small HTTP response wrapper returned by the CLI helper."""

    status: int
    body: bytes


def build_signature_body(
    *,
    typed_data_path: str | Path,
    private_key: str | None = None,
    cesr_signature: str | None = None,
    wallet: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    """Build the JSON body for ``POST /presentations/{said}/signature``.

    If ``cesr_signature`` is supplied, it is forwarded as-is. Otherwise the
    private key signs the EIP-712 typed data and the resulting 65-byte Ethereum
    signature is immediately CESR-encoded for transport.
    """

    typed_data = json.loads(Path(typed_data_path).read_text())
    message = typed_data["message"]
    if chain_id is None:
        chain_id = int(message["chainId"])
    if wallet is None:
        wallet = str(message["wallet"])
    if cesr_signature is None:
        if not private_key:
            raise ValueError("private_key is required when cesr_signature is not supplied")
        signable = encode_typed_data(full_message=typed_data)
        signature = Account.sign_message(signable, private_key).signature
        cesr_signature = encode_eth_signature_cesr(signature)
    return {
        "signatureType": WALLET_SIGNATURE_TYPE,
        "signature": cesr_signature,
        "wallet": wallet,
        "chainId": chain_id,
    }


def post_signature(
    *,
    oracle_url: str,
    wallet_said: str,
    body: dict[str, Any],
    timeout: float = 30.0,
) -> HttpResult:
    """POST the wallet signature body to the oracle presentation endpoint."""

    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    return _post_json(url=f"{oracle_url.rstrip('/')}/presentations/{wallet_said}/signature", body=raw, timeout=timeout)


def _post_json(*, url: str, body: bytes, timeout: float) -> HttpResult:
    req = request.Request(url=url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as rep:
            return HttpResult(status=rep.status, body=rep.read())
    except error.HTTPError as exc:
        body = exc.read()
        raise RuntimeError(f"POST {url} returned {exc.code}: {body.decode('utf-8')}") from exc

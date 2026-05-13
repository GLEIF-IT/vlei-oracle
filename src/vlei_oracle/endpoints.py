"""Falcon endpoints for the HIO-backed vLEI Oracle service."""

from __future__ import annotations

import json
from typing import Any

import falcon
from keri.app import indirecting

from .basing import OracleBaser, STATE_FAILED, STATE_POSTED
from .constants import WALLET_SIGNATURE_TYPE
from .wallet import decode_eth_signature_cesr


def load_ends(app: falcon.App, *, baser: OracleBaser, rxbs: bytearray, mbx) -> None:
    app.add_route("/", indirecting.HttpEnd(rxbs=rxbs, mbx=mbx))
    sig_end = SignatureEnd(baser=baser)
    status_end = PresentationStatusEnd(baser=baser)
    app.add_route("/presentations/{wallet_said}/signature", sig_end)
    app.add_route("/presentations/{wallet_said}", status_end)
    app.add_route("/health", HealthEnd(baser=baser))


class SignatureEnd:
    """Accept the second-step CESR-encoded Ethereum wallet signature."""

    def __init__(self, *, baser: OracleBaser):
        self.baser = baser

    def on_post(self, req: falcon.Request, rep: falcon.Response, wallet_said: str) -> None:
        body = _media(req)
        signature_type = _required_str(body, "signatureType")
        if signature_type != WALLET_SIGNATURE_TYPE:
            raise falcon.HTTPBadRequest(description="unsupported signatureType")
        wallet = _required_str(body, "wallet")
        chain_id = _required_int(body, "chainId")
        cesr_signature = _required_str(body, "signature")
        try:
            signature = decode_eth_signature_cesr(cesr_signature)
        except ValueError as exc:
            raise falcon.HTTPBadRequest(description=str(exc)) from exc

        op = self.baser.get_operation(wallet_said)
        if op is None:
            raise falcon.HTTPNotFound(description=f"presentation {wallet_said} not found")
        if op.state == STATE_FAILED:
            raise falcon.HTTPConflict(description=op.failureReason or "presentation failed")
        if op.state == STATE_POSTED:
            if op.signature == signature and op.wallet.lower() == wallet.lower() and op.chainId == chain_id:
                _json(rep, op.to_json(), status=falcon.HTTP_202)
                return
            raise falcon.HTTPConflict(description="presentation already posted")

        try:
            op = self.baser.attach_signature(
                wallet_said=wallet_said,
                signature_type=signature_type,
                signature=signature,
                wallet=wallet,
                chain_id=chain_id,
            )
        except KeyError as exc:
            raise falcon.HTTPNotFound(description=f"presentation {wallet_said} not found") from exc
        except ValueError as exc:
            raise falcon.HTTPConflict(description=str(exc)) from exc
        _json(rep, op.to_json(), status=falcon.HTTP_202)


class PresentationStatusEnd:
    def __init__(self, *, baser: OracleBaser):
        self.baser = baser

    def on_get(self, req: falcon.Request, rep: falcon.Response, wallet_said: str) -> None:
        _ = req
        op = self.baser.get_operation(wallet_said)
        if op is None:
            raise falcon.HTTPNotFound(description=f"presentation {wallet_said} not found")
        _json(rep, op.to_json())


class HealthEnd:
    def __init__(self, *, baser: OracleBaser):
        self.baser = baser

    def on_get(self, req: falcon.Request, rep: falcon.Response) -> None:
        _ = req
        counts: dict[str, int] = {}
        for _, op in self.baser.ops.getItemIter():
            counts[op.state] = counts.get(op.state, 0) + 1
        _json(rep, {"ok": True, "operations": counts})


def _media(req: falcon.Request) -> dict[str, Any]:
    try:
        body = req.get_media()
    except Exception as exc:
        raise falcon.HTTPBadRequest(description="request body must be JSON") from exc
    if not isinstance(body, dict):
        raise falcon.HTTPBadRequest(description="request body must be a JSON object")
    return body


def _required_str(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise falcon.HTTPBadRequest(description=f"{key} is required")
    return value


def _required_int(body: dict[str, Any], key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool):
        raise falcon.HTTPBadRequest(description=f"{key} must be an integer")
    if isinstance(value, int):
        return value
    raise falcon.HTTPBadRequest(description=f"{key} must be an integer")


def _json(rep: falcon.Response, data: dict[str, Any], *, status: str = falcon.HTTP_200) -> None:
    rep.status = status
    rep.content_type = "application/json"
    rep.data = json.dumps(data, sort_keys=True).encode("utf-8")

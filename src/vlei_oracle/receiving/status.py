"""Authenticated webhook acceptance and ACK ownership for the oracle inbox."""

import json
from urllib.parse import urlsplit

import falcon

from vlei_oracle.associations.statusing import read_status
from vlei_oracle.receiving.authing import AuthenticationError


class StatusEnd:
    """Borrow transport authentication and storage; ACK only committed events."""

    def __init__(self, *, auth, baser, target, chain_id):
        self.auth, self.baser = auth, baser
        self.target, self.chain_id = target, chain_id
        parts = urlsplit(target)
        self.relative_uri = parts.path + ("?" + parts.query if parts.query else "")

    def on_post(self, req, rep):
        """Accept a signed v1 JSON status POST at the configured destination.

        req supplies exact body bytes and KERI Signature/Signature-Input
        headers. HTTP 200 returns signed {eventId, stored: true}, bound to this
        request attempt, only after inbox, status and selection commit together. Duplicate
        events get a new ACK without repeated effects. ACK confirms durable storage, not ownership
        approval or an on-chain endorsement. Return 401 for authentication
        or replay failure, 400 for malformed events, and 409 for revision conflicts;
        errors never carry a success ACK. No credential or Ethereum RPC work occurs.
        """
        if req.relative_uri != self.relative_uri:
            raise falcon.HTTPBadRequest(
                description="destination does not match configuration"
            )
        body = req.bounded_stream.read()
        try:
            requestAuth = self.auth.verify(
                method=req.method,
                target=self.target,
                action="status",
                body=body,
                headers=req.headers,
            )
        except AuthenticationError as exc:
            raise falcon.HTTPUnauthorized(description=str(exc)) from exc
        try:
            event, status = read_status(body, chain_id=self.chain_id)
        except ValueError as exc:
            raise falcon.HTTPBadRequest(description=str(exc)) from exc
        try:
            self.baser.receive_status(event, status)
        except ValueError as exc:
            raise falcon.HTTPConflict(description=str(exc)) from exc

        # A crash or signing failure here leaves a committed event safe to redeliver.
        ack = json.dumps({"eventId": event.eventId, "stored": True}).encode()
        headers, _ = self.auth.sign(
            method=req.method,
            target=self.target,
            action="ack",
            body=ack,
            request=requestAuth.reference,
            status=200,
        )
        rep.status, rep.data = falcon.HTTP_200, ack
        rep.set_headers(headers)

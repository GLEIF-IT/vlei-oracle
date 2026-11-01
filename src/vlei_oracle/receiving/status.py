"""Authenticated webhook acceptance and ACK ownership for the oracle inbox."""

import json
from urllib.parse import urlsplit

import falcon

from vlei_oracle.associations.statusing import read_status
from vlei_oracle.receiving.authing import AuthenticationError


class StatusEnd:
    """Borrow authentication and the status-event service; ACK only committed events."""

    def __init__(self, *, auth, service, target, chain_id, verifier_aid, verifier_oobi):
        self.auth, self.service = auth, service
        self.verifier_oobi = verifier_oobi
        self.target, self.chain_id, self.verifier_aid = target, chain_id, verifier_aid
        parts = urlsplit(target)
        self.relative_uri = parts.path + ("?" + parts.query if parts.query else "")

    def on_post(self, req, rep):
        """Verify the configured producer and commit before signing the storage result.

        Exact redelivery returns success without repeated effects. The signed JSON
        result confirms Oracle storage, not selected ownership or publication.
        Return 503 while the producer introduction is unresolved, 401 for
        invalid/untrusted signatures, 400 for malformed events and
        409 for immutable-body or revision conflicts. No transport nonce is claimed.
        """
        if req.relative_uri != self.relative_uri:
            raise falcon.HTTPBadRequest(
                description="destination does not match configuration"
            )
        if not self.verifier_oobi.ready():
            raise falcon.HTTPServiceUnavailable(
                description="Verifier OOBI resolution is not ready", retry_after=1
            )
        statusEventBytes = req.bounded_stream.read()
        try:
            self.auth.verify(
                method=req.method,
                target=self.target,
                expected_aid=self.verifier_aid,
                body=statusEventBytes,
                headers=req.headers,
            )
        except AuthenticationError as exc:
            raise falcon.HTTPUnauthorized(description=str(exc)) from exc
        try:
            statusEvent, associationStatus = read_status(
                statusEventBytes, chain_id=self.chain_id
            )
        except ValueError as exc:
            raise falcon.HTTPBadRequest(description=str(exc)) from exc
        try:
            self.service.receive_status(statusEvent, associationStatus)
        except ValueError as exc:
            raise falcon.HTTPConflict(description=str(exc)) from exc

        # A crash or signing failure here leaves a committed event safe to redeliver.
        ackBytes = json.dumps({"eventId": statusEvent.eventId, "stored": True}).encode()
        headers = self.auth.sign(
            method=req.method,
            target=self.target,
            body=ackBytes,
        )
        rep.status, rep.data = falcon.HTTP_200, ackBytes
        rep.set_headers(headers)

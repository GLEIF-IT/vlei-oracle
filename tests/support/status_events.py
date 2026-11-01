"""support/status_events.py."""

import json

from falcon import testing
from keri.core import coring

from vlei_oracle.persistence.associations import AssociationReader


def said(label):
    """Make a canonical digest identifier for fixture facts, not a verified credential."""
    return coring.Diger(ser=label.encode()).qb64


def post(peers, body, *, auth=None, headers=None):
    """Send exact bytes through Falcon, signing each application retry anew."""
    if headers is None:
        headers, _ = (auth or peers.auth).sign(
            method="POST",
            target=peers.config.webhook_url,
            action="status",
            body=body,
        )
    return testing.TestClient(peers.receiver.app).simulate_post(
        "/v1/status-events", body=body, headers=headers
    )


def event_body(peers, label, sequence, *, issuer=None, revoked=False):
    """Make post-verification fixture facts; this helper does not assert credential validity."""
    body = json.loads(peers.event.body)
    body["associationSaid"] = said(label)
    body["statusRevision"] = 2 if revoked else 1
    body["eventId"] = f"{body['associationSaid']}:{body['statusRevision']}"
    body["association"] = {
        **body["association"],
        "issuerAid": issuer or peers.hab.pre,
        "leSaid": said(
            f"LE-{label}"
        ),  # LE credential SAID is not the holder's identity.
        "issuanceAnchor": {"sequenceNumber": sequence, "said": said(f"anchor-{label}")},
    }
    if revoked:
        body.update(status="revoked", reasonCode="REVOKED")
    return json.dumps(body).encode()


def selected(peers):
    """Read the selected association for the fixture's canonical chain/address."""
    return AssociationReader(peers.inbox).selected_association(
        json.loads(peers.event.body)["association"]["extId"]
    )

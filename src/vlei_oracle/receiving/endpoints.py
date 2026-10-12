"""KERI direct-mode ingestion and durable association status endpoints."""

from __future__ import annotations

import json
from typing import Any

import falcon
from keri.app import indirecting

from vlei_oracle.basing import OracleBaser


def load_ends(app: falcon.App, *, baser: OracleBaser, rxbs: bytearray, mbx) -> None:
    """Register KERI ingestion, association status, and health endpoints."""

    app.add_route("/", indirecting.HttpEnd(rxbs=rxbs, mbx=mbx))
    status_end = PresentationStatusEnd(baser=baser)
    app.add_route("/presentations/{association_said}", status_end)
    app.add_route("/health", HealthEnd(baser=baser))


class PresentationStatusEnd:
    """Expose durable operation state for polling clients and e2e checkpoints."""

    def __init__(self, *, baser: OracleBaser):
        self.baser = baser

    def on_get(
        self, req: falcon.Request, rep: falcon.Response, association_said: str
    ) -> None:
        """Return the current durable status for one wallet presentation."""

        _ = req
        op = self.baser.presentation_status(association_said)
        if op is None:
            raise falcon.HTTPNotFound(
                description=f"presentation {association_said} not found"
            )
        _json(rep, op)


class HealthEnd:
    """Expose a lightweight service liveness and operation-count response."""

    def __init__(self, *, baser: OracleBaser):
        self.baser = baser

    def on_get(self, req: falcon.Request, rep: falcon.Response) -> None:
        """Return service liveness plus operation counts grouped by state."""

        _ = req
        counts: dict[str, int] = {}
        for _, op in self.baser.verifications.getItemIter():
            counts[op.state] = counts.get(op.state, 0) + 1
        managed = {}
        for _, member in self.baser.managed.getItemIter():
            managed[member.state] = managed.get(member.state, 0) + 1
        _json(rep, {"ok": True, "verification": counts, "management": managed})


def _json(
    rep: falcon.Response, data: dict[str, Any], *, status: str = falcon.HTTP_200
) -> None:
    """Set the response status and encode its body as JSON."""
    rep.status = status
    rep.content_type = "application/json"
    rep.data = json.dumps(data, sort_keys=True).encode("utf-8")

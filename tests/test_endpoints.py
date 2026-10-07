"""The service receives finished associations and exposes durable status."""

import falcon
import pytest
from falcon import testing

from vlei_oracle.basing import OracleBaser
from vlei_oracle.constants import ASSOCIATION_SCHEMA
from vlei_oracle.endpoints import load_ends


@pytest.fixture
def service():
    baser = OracleBaser(name="test-status", temp=True)
    app = falcon.App()
    load_ends(app, baser=baser, rxbs=bytearray(), mbx=None)
    try:
        yield baser, testing.TestClient(app)
    finally:
        baser.close(clear=True)


def test_status_and_health_read_durable_association_work(service):
    baser, client = service
    op = baser.start_operation(
        association_said="association",
        grant_said="grant",
        grant_sender="issuer",
        association_schema=ASSOCIATION_SCHEMA,
    )
    assert client.simulate_get("/presentations/association").json == op.to_json()
    assert client.simulate_get("/health").json["operations"] == {"verifying": 1}
    assert client.simulate_get("/presentations/missing").status_code == 404


def test_signature_attachment_route_is_absent(service):
    _, client = service
    assert (
        client.simulate_post(
            "/presentations/association/signature", json={}
        ).status_code
        == 404
    )

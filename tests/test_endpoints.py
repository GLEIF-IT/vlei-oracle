"""The service receives finished associations and exposes durable status."""

import falcon
import pytest
from falcon import testing

from vlei_oracle.basing import OracleBaser, VerificationWork
from vlei_oracle.verification.constants import ASSOCIATION_SCHEMA
from vlei_oracle.receiving.endpoints import load_ends


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
    op = baser.start_verification(
        association_said="association",
        grant_said="grant")
    assert client.simulate_get("/associations/association").json == baser.association_status(op.associationSaid)
    assert client.simulate_get("/health").json["verification"] == {"verifying": 1}
    assert client.simulate_get("/associations/missing").status_code == 404


def test_signature_attachment_route_is_absent(service):
    _, client = service
    assert (
        client.simulate_post(
            "/associations/association/signature", json={}
        ).status_code
        == 404
    )


def test_management_does_not_overwrite_verification_status(service):
    baser, client = service
    work = baser.start_verification(association_said="association", grant_said="grant")
    baser.complete_verification(work)
    member = baser.get_managed("association")
    member.state = "withdrawn"
    baser.pin_managed(member)
    status = client.simulate_get("/associations/association").json
    assert status["verificationState"] == "verified"
    assert status["state"] == "withdrawn" and status["managed"]
    assert client.simulate_get("/health").json == {
        "ok": True, "verification": {}, "management": {"withdrawn": 1},
    }

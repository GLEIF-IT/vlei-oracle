"""Durable Grant idempotency does not reset completed or rejected operations."""

import pytest

from vlei_oracle.basing import OracleBaser
from vlei_oracle.constants import ASSOCIATION_SCHEMA


@pytest.mark.parametrize(
    "state", ["verifying", "awaiting_artifacts", "verified", "failed"]
)
def test_duplicate_grant_preserves_durable_progress(state, tmp_path):
    baser = OracleBaser(
        name="test-grant-idempotency", temp=False, headDirPath=str(tmp_path)
    )
    try:
        op = baser.start_operation(
            association_said="association",
            grant_said="first",
            grant_sender="issuer",
            association_schema=ASSOCIATION_SCHEMA,
        )
        op.state = state
        op.failureReason = "existing evidence"
        baser.pin_operation(op)
        duplicate = baser.start_operation(
            association_said="association",
            grant_said="duplicate",
            grant_sender="issuer",
            association_schema=ASSOCIATION_SCHEMA,
        )
        assert duplicate.state == state
        assert duplicate.grantSaid == "first"
        assert duplicate.failureReason == "existing evidence"
        baser.close()
        baser.reopen()
        assert baser.get_operation("association") == duplicate
    finally:
        baser.close(clear=True)

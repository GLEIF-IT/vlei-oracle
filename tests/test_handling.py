"""Durable Grant idempotency does not reset completed or rejected operations."""

import pytest

from vlei_oracle.basing import OracleBaser
from vlei_oracle.verification.constants import ASSOCIATION_SCHEMA


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


def test_false_acdc_said_cannot_poison_an_operation(tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch

    from keri import kering

    from vlei_oracle.receiving.grants import GrantNotificationDoer

    # Only the envelope lookup is stubbed. Native ACDC validation must reject
    # the claimed identity before replay or creating durable work for it.
    association = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "protocol/external-association-v1/eoa-v1/association.json"
        ).read_text()
    )
    claimed_said = association["d"]
    association["u"] = "X" + association["u"][1:]
    exn = SimpleNamespace(ked={"i": "sender", "e": {"acdc": association}})
    baser = OracleBaser(
        name="test-identity-poisoning", temp=False, headDirPath=str(tmp_path)
    )
    try:
        doer = GrantNotificationDoer(
            baser=baser,
            hby=None,
            notifier=None,
            parser=None,
            expected_association_schema=ASSOCIATION_SCHEMA,
        )
        with patch(
            "vlei_oracle.receiving.grants.exchanging.cloneMessage", return_value=(exn, {})
        ), pytest.raises(kering.ValidationError):
            doer.process_grant_notice({"d": "grant"})
        assert baser.get_operation(claimed_said) is None
    finally:
        baser.close(clear=True)

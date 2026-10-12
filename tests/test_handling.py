"""Durable Grant idempotency does not reset completed or rejected operations."""

import pytest

from vlei_oracle.basing import OracleBaser, VerificationWork
from vlei_oracle.verification.constants import ASSOCIATION_SCHEMA


@pytest.mark.parametrize(
    "state", ["verifying", "awaiting_artifacts", "failed"]
)
def test_duplicate_grant_preserves_durable_progress(state, tmp_path):
    baser = OracleBaser(
        name="test-grant-idempotency", temp=False, headDirPath=str(tmp_path)
    )
    try:
        op = baser.start_verification(
            association_said="association",
            grant_said="first")
        op.state = state
        op.failureReason = "existing evidence"
        baser.pin_verification(op)
        duplicate = baser.start_verification(
            association_said="association",
            grant_said="duplicate")
        assert duplicate.state == state
        assert duplicate.grantSaid == "first"
        assert duplicate.failureReason == "existing evidence"
        baser.close()
        baser.reopen()
        assert baser.get_verification("association") == duplicate
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
        assert baser.get_verification(claimed_said) is None
    finally:
        baser.close(clear=True)


@pytest.mark.parametrize("interruption", ["during_commit", "after_commit"])
def test_verification_handoff_survives_process_crash(tmp_path, interruption):
    import subprocess
    import sys

    store = OracleBaser(name="handoff", headDirPath=str(tmp_path))
    store.start_verification(association_said="association", grant_said="grant")
    store.close()
    script = """
import os, sys
from vlei_oracle.basing import OracleBaser
store = OracleBaser(name="handoff", headDirPath=sys.argv[1])
if sys.argv[2] == "during_commit":
    # Crash after verification is removed inside the native transaction,
    # before membership serialization. No committed half-handoff is allowed.
    store.managed.serializer = lambda value: os._exit(17)
store.complete_verification(store.get_verification("association"))
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), interruption])
    assert result.returncode == 17
    store.reopen()
    try:
        work = store.get_verification("association")
        if interruption == "during_commit":
            assert work.state == "verifying"
            assert store.get_managed("association") is None
            store.complete_verification(work)
        else:
            assert work is None
            assert store.get_managed("association") is not None
        member = store.get_managed("association")
        member.state = "withdrawn"
        store.pin_managed(member)
        # Repeated completion and reordered Grants cannot revive withdrawn work.
        store.complete_verification(work if work is not None else VerificationWork(associationSaid="association"))
        store.start_verification(association_said="association", grant_said="reordered")
        assert store.get_managed("association") == member
    finally:
        store.close(clear=True)


def test_failed_verification_cannot_enroll(tmp_path):
    store = OracleBaser(name="rejected", headDirPath=str(tmp_path))
    try:
        work = store.start_verification(association_said="association", grant_said="grant")
        store.mark_failed(work, "invalid proof")
        with pytest.raises(ValueError, match="unfinished verification"):
            store.complete_verification(work)
        assert store.get_managed("association") is None
    finally:
        store.close(clear=True)

"""Supported Oracle commands, typed configuration and offline recovery against real stores."""

import pytest

from vlei_oracle.runtime.cli import main


def test_combined_service_command_is_rejected(capsys):
    """Reject the deleted combined runtime rather than restoring credential verification in Oracle."""
    with pytest.raises(SystemExit) as error:
        main(["server", "legacy-start"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["challenge", "client"])
def test_issuer_commands_are_absent(command, capsys):
    """Keep issuer-only operations out of the trusted status receiver CLI."""
    with pytest.raises(SystemExit) as error:
        main([command])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.fixture
def receiver_config(tmp_path):
    """Supply canonical trust/identity input and isolated storage without starting any process."""
    from keri import core

    from vlei_oracle.runtime.config import ReceiverConfig

    return ReceiverConfig(
        verifier_aid=core.Signer().verfer.qb64,
        chain_id=31337,
        webhook_url="https://oracle.example/v1/status-events",
        verifier_oobi="https://verifier.example/oobi/producer",
        name="operator",
        base="ops",
        head_dir=str(tmp_path),
        alias="signer",
        passcode="fixture-only",
        incept_file="inception.json",
        host="127.0.0.1",
        http_port=8888,
    )


@pytest.mark.parametrize("posting", [False, True])
def test_service_commands_pass_explicit_config(receiver_config, monkeypatch, posting):
    """Receive ignores posting secrets; start validates only its selected environment key and options."""
    from dataclasses import replace

    from vlei_oracle.runtime import cli

    configs = []
    monkeypatch.setattr(cli, "run_receiver", configs.append)
    monkeypatch.setenv("VLEI_ORACLE_POSTING_KEY", "invalid-unused-key")
    public_key = "0x" + "0" * 63 + "1"
    monkeypatch.setenv("TEST_POSTING_KEY", public_key)
    config = receiver_config
    args = [
        "server",
        "start" if posting else "receive",
        "--verifier-aid",
        config.verifier_aid,
        "--chain-id",
        str(config.chain_id),
        "--webhook-url",
        config.webhook_url,
        "--verifier-oobi",
        config.verifier_oobi,
        "--name",
        config.name,
        "--base",
        config.base,
        "--head-dir",
        config.head_dir,
        "--alias",
        config.alias,
        "--passcode",
        config.passcode,
        "--incept-file",
        config.incept_file,
        "--host",
        config.host,
        "--http-port",
        str(config.http_port),
    ]
    expected = config
    if posting:
        args += [
            "--rpc-url",
            "http://localhost:8546",
            "--endorsement-registry",
            "0x" + "12" * 20,
            "--identity-registry",
            "0x" + "34" * 20,
            "--posting-key-env",
            "TEST_POSTING_KEY",
            "--confirmations",
            "4",
            "--posting-interval",
            "2",
            "--max-attempts",
            "5",
            "--pending-timeout",
            "60",
        ]
        expected = replace(
            expected,
            rpc_url="http://localhost:8546",
            endorsement_registry="0x" + "12" * 20,
            identity_registry="0x" + "34" * 20,
            posting_key=public_key,
            confirmations=4,
            posting_interval=2.0,
            max_attempts=5,
            pending_timeout=60.0,
        )
    assert main(args) == 0
    assert configs == [expected]


@pytest.mark.parametrize("signed", [False, True])
def test_retry_reopens_selected_store_without_rpc(tmp_path, signed):
    """Resume one seeded offline operation, retaining its exact signed record/nonce when present."""
    from keri.db import dbing

    from vlei_oracle.persistence.basing import OracleBaser
    from vlei_oracle.persistence.records import (
        PostingOperation,
        PostingSequence,
        TransactionRecord,
    )

    options = dict(
        cls=OracleBaser,
        name="operator",
        base="ops",
        headDirPath=str(tmp_path),
        temp=False,
    )
    txRecord = TransactionRecord("tx", "0xdeadbeef", 8, "credential", "endorse")
    operation = PostingOperation(
        "0000000000000001",
        "credential:1",
        "credential",
        "wallet",
        attempts=3,
        rpcFailures=3,
        txHash="tx" if signed else "",
        error="stopped",
    )
    with dbing.openLMDB(**options) as baser:
        baser.dead.put(keys=(operation.operationId,), val=operation)
        baser.sequence.put(
            keys=("writer",),
            val=PostingSequence(1, operation.operationId if signed else ""),
        )
        if signed:
            baser.transactions.put(keys=(txRecord.txHash,), val=txRecord)
    arguments = [
        "retry",
        operation.operationId,
        "--name",
        "operator",
        "--base",
        "ops",
        "--head-dir",
        str(tmp_path),
    ]
    assert main(arguments) == 0
    with dbing.openLMDB(**options) as baser:
        assert baser.dead.get(keys=(operation.operationId,)) is None
        postingOp = (baser.pending if signed else baser.preparing).get(keys=("writer",))
        assert postingOp.operationId == operation.operationId
        assert (
            postingOp.attempts == postingOp.rpcFailures == 0 and postingOp.error == ""
        )
        assert postingOp.txHash == operation.txHash
        assert baser.transactions.get(keys=(txRecord.txHash,)) == (
            txRecord if signed else None
        )
        assert baser.sequence.get(keys=("writer",)) == PostingSequence(1, "")
    assert main(arguments) == 1  # Active work must prevent a second recovery attempt.

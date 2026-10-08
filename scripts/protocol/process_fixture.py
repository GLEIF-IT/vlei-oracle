"""Crash/restart the actual oracle CLI in isolated test namespaces."""

import os
import re
import subprocess
import sys
from dataclasses import replace
from uuid import uuid4

from hio.core import http

from transport_fixture import PresentationDoer


def start(config, *, stack, artifacts, workflow):
    """Start the CLI, wait through HIO for health, and read its actual AID."""
    command = [sys.executable, "-m", "vlei_oracle.cli", "server", "start"]
    for flag, value in (
        ("--name", config.name), ("--base", config.base), ("--alias", config.alias),
        ("--host", "127.0.0.1"), ("--http-port", config.http_port),
        ("--trusted-geda-aid", config.trusted_geda_aid),
        ("--rpc-url", config.rpc_url), ("--schema-dir", config.schema_dir),
    ):
        command.extend([flag, str(value)])
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if config.no_post:
        command.append("--no-post")
    else:
        for flag, value in (
            ("--chain-id", config.chain_id),
            ("--endorsement-registry", config.endorsement_registry),
            ("--identity-registry", config.identity_registry),
            ("--confirmations", config.confirmations),
            ("--posting-interval", config.posting_interval),
        ):
            command.extend([flag, str(value)])
        environment["VLEI_ORACLE_POSTING_KEY"] = config.posting_key
    log_path = artifacts / ("oracle-process-" + uuid4().hex + ".log")
    log = stack.enter_context(log_path.open("w"))
    process = stack.enter_context(subprocess.Popen(
        command, env=environment, stdout=log, stderr=subprocess.STDOUT,
    ))
    stack.callback(process.terminate)
    client = http.Client(hostname="127.0.0.1", port=config.http_port, reconnectable=True)
    doer = http.ClientDoer(client=client)
    workflow.extend([doer])
    try:
        client.request(method="GET", path="/health")
        deadline = workflow.tyme + 30
        while not client.responses:
            assert process.poll() is None, log_path.read_text()
            assert workflow.tyme < deadline, "oracle CLI did not become healthy: " + log_path.read_text()
            yield 0.01
        response = client.responses.popleft()
        assert response["status"] == 200, response
        match = re.search(r"vlei-oracle serving [^:]+:([^ ]+) on ", log_path.read_text())
        assert match, log_path.read_text()
        return process, match[1]
    finally:
        workflow.remove([doer])


def crash(process):
    """Deliberately lose the process's queues/cues without graceful shutdown."""
    process.kill()
    while process.poll() is None:
        yield 0.01


def qualify_verification(*, config, stack, artifacts, workflow, partial,
                         complete, association_said, working_rpc):
    """Recover missing-artifact and interrupted RPC work without a new Grant."""
    print(f"Retaining process-test stores: --name {config.name} --base {config.base}", flush=True)
    process, aid = yield from start(config, stack=stack, artifacts=artifacts, workflow=workflow)
    first = PresentationDoer(
        port=config.http_port, recipient=aid, stream=partial(aid),
        said=association_said, expected="awaiting_artifacts",
    )
    workflow.extend([first])
    while not first.done:
        yield 0.01
    workflow.remove([first])
    yield from crash(process)
    process, reopened_aid = yield from start(config, stack=stack, artifacts=artifacts, workflow=workflow)
    assert reopened_aid == aid
    interrupted = PresentationDoer(
        port=config.http_port, recipient=aid, stream=complete,
        said=association_said, expected="verifying",
    )
    workflow.extend([interrupted])
    while not interrupted.done:
        yield 0.01
    workflow.remove([interrupted])
    assert interrupted.result["createdAt"] == first.result["createdAt"]
    assert not interrupted.result["verified"]
    yield from crash(process)
    config = replace(config, rpc_url=working_rpc)
    process, reopened_aid = yield from start(config, stack=stack, artifacts=artifacts, workflow=workflow)
    assert reopened_aid == aid
    recovered = PresentationDoer(
        port=config.http_port, recipient=aid, stream=b"",
        said=association_said, expected="verified",
    )
    workflow.extend([recovered])
    while not recovered.done:
        yield 0.01
    workflow.remove([recovered])
    assert recovered.result["createdAt"] == first.result["createdAt"]
    assert recovered.result["verified"]["associationSaid"] == association_said
    yield from crash(process)
    print("Independent oracle processes recover missing evidence and interrupted RPC without a new Grant", flush=True)
    return config, aid, recovered.result

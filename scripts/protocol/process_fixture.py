"""Crash/restart the actual oracle CLI in isolated test namespaces."""

import os
import json
import re
import subprocess
import sys
from dataclasses import replace
from uuid import uuid4

from hio.core import http
from hio.core.http.clienting import backendRequest
import falcon
from eth_account import Account

from vlei_oracle.basing import OracleBaser

from transport_fixture import PresentationDoer
from contract_fixture import DEPLOYER_KEY, contract, read, send


class SubmissionBarrier:
    """Forward real reads through HIO WSGI streaming; hold broadcasts."""

    def __init__(self, *, rpc_url, tymth):
        self.rpc_url, self.tymth = rpc_url, tymth
        self.attempted_raw = None

    def on_post(self, req, resp):
        request = req.media
        if request["method"] == "eth_sendRawTransaction":
            self.attempted_raw = request["params"][0]
            resp.media = {"jsonrpc": "2.0", "id": request["id"],
                          "error": {"code": -32603, "message": "qualification submission hold"}}
        else:
            resp.content_type = "application/json"
            resp.stream = self.forward(request)

    def forward(self, request):
        response = yield from backendRequest(
            self.tymth, method="POST", path=self.rpc_url, data=request, tymeout=5,
        )
        if response is None or response["status"] != 200:
            yield json.dumps({"jsonrpc": "2.0", "id": request["id"],
                              "error": {"code": -32603, "message": "backend unavailable"}}).encode()
        else:
            yield bytes(response["body"])


def start(config, *, stack, artifacts, workflow):
    """Start the CLI, wait through HIO for health, and read its actual AID."""
    command = [sys.executable, "-m", "vlei_oracle.cli", "server", "legacy-start"]
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
    assert not interrupted.result["managed"]
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
    assert recovered.result["associationSaid"] == association_said and recovered.result["managed"]
    yield from crash(process)
    print("Independent oracle processes recover missing evidence and interrupted RPC without a new Grant", flush=True)
    return config, aid, recovered.result


def qualify_before_submission(*, config, effects, association_said, proxy_port,
                              stack, artifacts, workflow):
    """Crash after persisted intent reaches the submission barrier, then recover."""
    barrier = SubmissionBarrier(rpc_url=effects.rpc_url, tymth=workflow.tymth)
    app = falcon.App()
    app.add_route("/", barrier)
    server = http.Server(host="127.0.0.1", port=proxy_port, app=app)
    doer = http.ServerDoer(server=server)
    workflow.extend([doer])
    held = replace(config, rpc_url=f"http://127.0.0.1:{proxy_port}")
    process, aid = yield from start(held, stack=stack, artifacts=artifacts, workflow=workflow)
    deadline = workflow.tyme + 30
    while barrier.attempted_raw is None:
        assert process.poll() is None, "oracle exited before the submission barrier"
        assert workflow.tyme < deadline, "oracle did not reach the submission barrier"
        yield 0.01
    yield from crash(process)
    workflow.remove([doer])
    store = OracleBaser(name=config.name, base=config.base, readonly=True)
    try:
        intent = store.unfinished_transaction()
        assert intent and intent.rawTransaction == barrier.attempted_raw
    finally:
        store.close()
    assert (yield from effects.request("eth_getTransactionByHash", [intent.txHash])) is None
    process, reopened_aid = yield from start(config, stack=stack, artifacts=artifacts, workflow=workflow)
    assert reopened_aid == aid
    check = PresentationDoer(
        port=config.http_port, recipient=aid, stream=b"",
        said=association_said, expected="endorsed",
    )
    workflow.extend([check])
    while not check.done:
        yield 0.01
    workflow.remove([check])
    assert intent.txHash in [entry["txHash"] for entry in check.result["transactions"]]
    assert len(check.result["transactions"]) == 2
    nonce = yield from effects.request("eth_getTransactionCount", [Account.from_key(config.posting_key).address, "latest"])
    assert int(nonce, 16) == intent.nonce + 2
    print("Independent posting process recovers persisted intent before broadcast with no duplicate nonce", flush=True)
    return process, aid, check.result


def authorize_writer(*, effects, comply, key):
    """Fund and authorize a dedicated test writer, without wallet admission."""
    address = Account.from_key(key).address
    yield from effects.request("anvil_setBalance", [address, "0x56bc75e2d63100000"])
    admin_address = (yield from read(effects, effects.contracts["endorsement"], "adminControls"))[0]
    admin = contract(comply, "AdminControls", admin_address)
    yield from send(effects, admin, "grantRole", [
        effects.web3.keccak(text="COMPLIANCE_ADMIN_ROLE"), address,
    ], DEPLOYER_KEY)


def qualify_after_submission(*, config, effects, association_said, evidence,
                             stack, artifacts, workflow):
    """Crash with a real pending transaction, mine it, and recover by saved hash."""
    writer = Account.from_key(config.posting_key).address
    baseline = int((yield from effects.request("eth_getTransactionCount", [writer, "latest"])), 16)
    yield from effects.request("anvil_setAutomine", [False])
    process, aid = yield from start(config, stack=stack, artifacts=artifacts, workflow=workflow)
    check = PresentationDoer(
        port=config.http_port, recipient=aid, stream=evidence(aid),
        said=association_said, expected="verified",
    )
    workflow.extend([check])
    while not check.done:
        yield 0.01
    workflow.remove([check])
    deadline = workflow.tyme + 30
    while True:
        nonce = yield from effects.request("eth_getTransactionCount", [writer, "pending"])
        if int(nonce, 16) > baseline:
            break
        assert process.poll() is None, "oracle exited before broadcasting"
        assert workflow.tyme < deadline, "oracle did not broadcast a pending transaction"
        yield 0.01
    yield from crash(process)
    store = OracleBaser(name=config.name, base=config.base, readonly=True)
    try:
        intent = store.unfinished_transaction()
        assert intent and intent.nonce == baseline
    finally:
        store.close()
    transaction = yield from effects.request("eth_getTransactionByHash", [intent.txHash])
    assert transaction and transaction["blockHash"] is None
    yield from effects.request("anvil_setAutomine", [True])
    yield from effects.request("evm_mine", [])
    process, reopened_aid = yield from start(config, stack=stack, artifacts=artifacts, workflow=workflow)
    assert reopened_aid == aid
    recovered = PresentationDoer(
        port=config.http_port, recipient=aid, stream=b"",
        said=association_said, expected="endorsed",
    )
    workflow.extend([recovered])
    while not recovered.done:
        yield 0.01
    workflow.remove([recovered])
    assert intent.txHash in [entry["txHash"] for entry in recovered.result["transactions"]]
    assert len(recovered.result["transactions"]) == 2
    nonce = yield from effects.request("eth_getTransactionCount", [writer, "latest"])
    assert int(nonce, 16) == baseline + 2
    print("Independent posting process recovers after broadcast and lost receipt without a duplicate nonce", flush=True)
    return process, aid, recovered.result

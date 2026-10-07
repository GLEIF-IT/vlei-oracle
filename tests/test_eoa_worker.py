"""Exercise real HIO HTTP scheduling and durable verification transitions."""

import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import falcon
import pytest
from hio.base import doing
from hio.core import http, tcp

from vlei_oracle.basing import OperationRecord, OracleBaser
from vlei_oracle.eoa_worker import EoaVerifierDoer
from vlei_oracle.handling import PresentationVerifierDoer
from vlei_oracle.verifier import VerificationError

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "protocol/external-association-v1/eoa-v1/association.json"
)


def free_port():
    """Choose an available loopback port number for a test server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RpcEndpoint:
    """Serve controlled chain facts through a real HTTP connection."""

    def __init__(self, *, chain="0x1", code="0x", unavailable=False):
        self.chain, self.code, self.unavailable = chain, code, unavailable
        self.calls = []

    def on_post(self, req, resp):
        request = req.media
        self.calls.append(request["method"])
        if self.unavailable:
            resp.status = falcon.HTTP_503
            return
        resp.media = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": self.chain if request["method"] == "eth_chainId" else self.code,
        }


@pytest.fixture
def rpc():
    endpoint = RpcEndpoint()
    app = falcon.App()
    app.add_route("/", endpoint)
    server = http.Server(host="127.0.0.1", port=free_port(), app=app)
    server.reopen()
    try:
        yield endpoint, server, f"http://127.0.0.1:{server.servant.ha[1]}"
    finally:
        server.close()


def run_until(doers, complete, *, limit=2.0):
    """Drive real sockets and stop children as soon as the assertion is ready."""
    ticks = []

    def observe(tymth, tock=0.0, **opts):
        yield tock
        while not complete():
            ticks.append(tymth())
            yield 0.01
        parent.remove(doers)
        return True

    parent = doing.DoDoer(doers=[*doers, doing.doify(observe)])
    doing.Doist(real=True, tock=0.01, limit=limit).do(doers=[parent])
    assert parent.done, "HIO workflow did not complete"
    return ticks


@pytest.mark.parametrize(
    "chain,code,expected",
    [
        ("0x1", "0x", "verified"),
        ("0x1", "0xef0100" + "12" * 20, "verified"),
        ("0x2", "0x", "failed"),
        ("0x1", "0x6000", "failed"),
        ("0x1", "invalid", "retry"),
    ],
)
def test_worker_resolves_account_over_http(rpc, chain, code, expected):
    endpoint, server, url = rpc
    endpoint.chain, endpoint.code = chain, code
    worker = EoaVerifierDoer(rpc_url=url)
    association = json.loads(FIXTURE.read_text())
    worker.msgs.append(association)
    run_until([http.ServerDoer(server=server), worker], lambda: bool(worker.cues))
    cue = worker.cues.popleft()
    assert cue["said"] == association["d"]
    assert cue["kin"] == expected
    assert endpoint.calls == ["eth_chainId", "eth_getCode"]
    if expected == "verified":
        assert cue["verified"]["wallet"] == association["a"]["extId"].split(":")[2]


def test_rpc_timeout_yields_and_closes_client():
    # Accept TCP but never send an HTTP response. Other Doers must keep running.
    server = tcp.Server(host="127.0.0.1", port=free_port())
    server.reopen()
    worker = EoaVerifierDoer(rpc_url=f"http://127.0.0.1:{server.ha[1]}", timeout=0.15)
    worker.msgs.append(json.loads(FIXTURE.read_text()))
    try:
        ticks = run_until(
            [tcp.ServerDoer(server=server), worker], lambda: bool(worker.cues)
        )
        assert len(ticks) >= 10
        cue = worker.cues.popleft()
        assert cue["kin"] == "retry"
        assert "timed out" in cue["error"]
        assert not any(isinstance(child, http.ClientDoer) for child in worker.doers)
    finally:
        server.close()


@pytest.mark.parametrize("invalid_proof", ["changed_nonce", "unrecoverable_signature"])
def test_invalid_proof_never_requests_rpc(rpc, invalid_proof):
    endpoint, server, url = rpc
    worker = EoaVerifierDoer(rpc_url=url)
    association = json.loads(FIXTURE.read_text())
    if invalid_proof == "changed_nonce":
        association["u"] = "changed"
    else:
        # Valid scalar ranges do not guarantee a recoverable curve point.
        association["a"]["proof"]["sig"] = "0x" + f"{5:064x}{1:064x}" + "1b"
    worker.msgs.append(association)
    run_until([http.ServerDoer(server=server), worker], lambda: bool(worker.cues))
    assert worker.cues.popleft()["kin"] == "failed"
    assert endpoint.calls == []


@pytest.fixture
def operation(tmp_path):
    association = json.loads(FIXTURE.read_text())
    baser = OracleBaser(name="test-eoa-flow", temp=False, headDirPath=str(tmp_path))
    op = OperationRecord(walletCredentialSaid=association["d"], state="verifying")
    baser.pin_operation(op)
    try:
        yield baser, association
    finally:
        baser.close(clear=True)


def coordinator(baser, association, url):
    """Compose the real coordinator and worker around a test operation store."""
    return PresentationVerifierDoer(
        baser=baser,
        rgy=None,
        trusted_geda_aid="test-root",
        association_schema=association["s"],
        eoa_verifier=EoaVerifierDoer(rpc_url=url),
    )


@pytest.mark.parametrize("interruption", ["rpc_failure", "in_flight"])
def test_pending_work_survives_coordinator_restart(rpc, operation, interruption):
    endpoint, server, url = rpc
    baser, association = operation
    endpoint.unavailable = interruption == "rpc_failure"
    first = coordinator(baser, association, url)
    with patch(
        "vlei_oracle.handling.verify_association",
        return_value=SimpleNamespace(sad=association),
    ):
        run_until(
            [http.ServerDoer(server=server), first],
            lambda: (
                baser.get_operation(association["d"]).retryCount == 1
                if interruption == "rpc_failure"
                else bool(endpoint.calls)
            ),
        )
        pending = baser.get_operation(association["d"])
        assert pending.state == "verifying"
        assert not pending.verified
        if interruption == "rpc_failure":
            assert "503" in pending.failureReason
        assert not any(
            isinstance(child, http.ClientDoer) for child in first.eoa_verifier.doers
        )
        # Reopen LMDB as well: the next coordinator must use persisted work.
        baser.close()
        baser.reopen()

        # A fresh coordinator has no queues or pending flags from the first run.
        endpoint.unavailable = False
        second = coordinator(baser, association, url)
        run_until(
            [http.ServerDoer(server=server), second],
            lambda: baser.get_operation(association["d"]).state == "verified",
        )
    assert baser.get_operation(association["d"]).failureReason == ""
    assert endpoint.calls == ["eth_chainId", "eth_chainId", "eth_getCode"]


def test_revocation_during_rpc_prevents_success(rpc, operation):
    endpoint, server, url = rpc
    baser, association = operation
    verifier = coordinator(baser, association, url)

    def current_association(**kwargs):
        # Simulate TEL advancement while HTTP is in flight. The full issuance
        # exercise separately proves actual KERIpy issuance/revocation acceptance.
        if endpoint.calls:
            raise VerificationError("LE credential is revoked")
        return SimpleNamespace(sad=association)

    with patch(
        "vlei_oracle.handling.verify_association", side_effect=current_association
    ):
        run_until(
            [http.ServerDoer(server=server), verifier],
            lambda: baser.get_operation(association["d"]).state == "failed",
        )
    op = baser.get_operation(association["d"])
    assert not op.verified
    assert "revoked" in op.failureReason
    assert endpoint.calls == ["eth_chainId", "eth_getCode"]

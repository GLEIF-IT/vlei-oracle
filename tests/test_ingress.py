"""The installed ingress rejects oversized bodies before contacting the private backend."""

import json
import shutil
import socket
import subprocess
import time
from dataclasses import replace
from importlib.resources import files

import pytest
from hio.base import doing
from hio.core import http

from test_status_inbox import peers
from vlei_oracle.receiving.service import StatusReceiver

BODY_LIMIT = 64 * 1024


@pytest.fixture
def ingress(peers, tmp_path):
    """Run the packaged Nginx config on isolated ports with its buffering/limit intact."""
    executable = shutil.which("nginx")
    assert executable, "nginx is required for the ingress integration checks"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    target = f"http://127.0.0.1:{port}/v1/status-events?memo=a+b"
    receiver = StatusReceiver(
        replace(peers.config, webhook_url=target), hby=peers.oracle, baser=peers.inbox
    )
    template = files("vlei_oracle.receiving").joinpath("nginx.conf").read_text()
    config = tmp_path / "nginx.conf"
    config.write_text(template.replace("listen 9722;", f"listen 127.0.0.1:{port};").replace(
        "127.0.0.1:9723", f"127.0.0.1:{peers.config.http_port}"
    ))
    (tmp_path / "client_body").mkdir()
    with (tmp_path / "nginx.log").open("w+") as log:
        process = subprocess.Popen(
            [executable, "-p", str(tmp_path) + "/", "-c", str(config),
             "-g", "daemon off; master_process off;"],
            stdout=subprocess.DEVNULL, stderr=log,
        )
        try:
            deadline = time.monotonic() + 3
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "nginx did not start"
                    time.sleep(0.01)
            yield receiver, target, port
        finally:
            process.terminate()
            process.wait(timeout=3)


def test_receiver_refuses_public_backend_binding(peers):
    """The public proxy cannot be bypassed by binding the status backend to all interfaces."""
    with pytest.raises(ValueError, match="loopback"):
        replace(peers.config, host="0.0.0.0")


@pytest.mark.parametrize("framing", ["content-length", "chunked"])
def test_oversized_body_never_contacts_backend(peers, ingress, framing):
    """Both declared and cumulative chunked excess return 413 without an upstream connection."""
    _, _, port = ingress
    # A listening socket records any attempted upstream connection before HIO could buffer it.
    with socket.socket() as backend:
        backend.bind(("127.0.0.1", peers.config.http_port))
        backend.listen()
        backend.settimeout(0.05)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            head = b"POST /v1/status-events HTTP/1.1\r\nHost: localhost\r\n"
            if framing == "content-length":
                client.sendall(head + f"Content-Length: {BODY_LIMIT + 1}\r\n\r\n".encode())
            else:
                # Each chunk is below the cap; their combined size crosses it.
                chunks = [b"x" * (BODY_LIMIT // 2), b"x" * (BODY_LIMIT // 2 + 1)]
                client.sendall(head + b"Transfer-Encoding: chunked\r\n\r\n" + b"".join(
                    f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n" for chunk in chunks
                ) + b"0\r\n\r\n")
            assert client.recv(4096).startswith(b"HTTP/1.1 413")
        with pytest.raises(socket.timeout):
            backend.accept()


def test_signed_event_at_body_limit_gets_authenticated_ack(peers, ingress):
    """A bounded real event retains its exact body/query/signature through Nginx and HIO."""
    receiver, target, _ = ingress
    body = peers.event.body.encode()
    body += b" " * (BODY_LIMIT - len(body))  # Legal JSON whitespace; sign these exact bytes.
    headers, sent = peers.auth.sign(method="POST", target=target, action="status", body=body)
    client = http.Client(path=target)
    client_doer = http.ClientDoer(client=client)
    responses = []

    def request(tymth, tock=0.0, **opts):
        """Deliver once, then verify the real receiver's correlated ACK before shutdown."""
        yield tock
        client.request(method="POST", headers=headers, body=body)
        while not client.responses:
            yield 0.01
        response = client.responses.popleft()
        peers.auth.verify(
            method="POST", target=target, action="ack", body=response["body"],
            headers=response["headers"], request=sent.reference, status=response["status"],
        )
        responses.append(response)
        parent.remove([receiver, client_doer])

    parent = doing.DoDoer(doers=[receiver, client_doer, doing.doify(request)])
    doing.Doist(real=True, tock=0.01, limit=3).do(doers=[parent])
    assert parent.done and responses[0]["status"] == 200
    assert json.loads(responses[0]["body"]) == {"eventId": peers.event.eventId, "stored": True}
    assert peers.inbox.inbox.get(keys=(peers.event.eventId,)).body.encode() == body

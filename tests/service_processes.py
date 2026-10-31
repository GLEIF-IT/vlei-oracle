"""Test-only CLI ownership and a transparent lost-ACK proxy for service qualification."""

from contextlib import closing
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import subprocess
import threading

from hio.core import http
from keri.app import configing, habbing, keeping
from keri.db import basing


def free_port():
    """Reserve an ephemeral loopback port briefly; the caller binds it promptly."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def identity(root, name, alias):
    """Initialize/reopen only the service identity using the CLI's KERIpy store layout.

    Return AID and public KERIpy KEL bytes. Contexts close before the CLI opens
    these stores; the fixture never reads a live process's writable database.
    """
    options = dict(name=name, headDirPath=str(root), temp=False)
    with (
        keeping.openKS(**options) as ks,
        basing.openDB(**options) as db,
        configing.openCF(**options) as cf,
    ):
        hby = habbing.Habery(name=name, ks=ks, db=db, cf=cf, temp=False, free=False)
        hab = hby.habByName(alias) or hby.makeHab(name=alias)
        return hab.pre, bytes(hab.makeOwnEvent(sn=0))


def launch(stack, command, log_path, *, env=None):
    """Start an installed CLI with its own log; stack stops it before fixture storage closes."""
    log = stack.enter_context(log_path.open("w"))
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    stack.callback(stop, process)
    return process


def stop(process):
    """Terminate only this fixture's CLI and reap it, including after assertion failure."""
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=10)


def ready(process, port, log_path, workflow):
    """Yield until a CLI opens its listener; report its log if startup fails."""
    deadline = workflow.tyme + 15
    while True:
        assert process.poll() is None, log_path.read_text()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return
        except OSError:
            assert workflow.tyme < deadline, log_path.read_text()
            yield 0.02


def request(workflow, port, path, *, method="GET", body=b""):
    """Own one HIO client through a bounded HTTP exchange; return status and exact bytes."""
    client = http.Client(hostname="127.0.0.1", port=port)
    doer = http.ClientDoer(client=client)
    workflow.extend([doer])
    try:
        client.request(method=method, path=path, body=body)
        deadline = workflow.tyme + 10
        while not client.responses:
            assert workflow.tyme < deadline, f"HTTP timeout: {method} {port}{path}"
            yield 0.01
        response = client.responses.popleft()
        return response["status"], bytes(response["body"])
    finally:
        workflow.remove([doer])


def lost_ack_proxy(stack, oracle_port):
    """Forward signed bytes unchanged and drop only the first successful ACK.

    The receiver is configured for this public destination. A real connection
    loss after its commit exercises the installed sender's retry, not a fake ACK.
    The returned server retains forwarded event bytes for duplicate assertions.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            with closing(
                HTTPConnection("127.0.0.1", oracle_port, timeout=5)
            ) as connection:
                connection.request("POST", self.path, body, dict(self.headers))
                response = connection.getresponse()
                content = response.read()
                self.server.events.append(body)
                if response.status == 200 and not self.server.dropped:
                    self.server.dropped = True
                    self.close_connection = True
                    return
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in (
                        "connection",
                        "transfer-encoding",
                        "server",
                        "date",
                        "content-length",
                    ):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        def log_message(self, format, *args):
            """Keep expected dropped-response noise out of the test report."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.events, server.dropped = [], False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stack.callback(server.server_close)
    stack.callback(thread.join, 5)
    stack.callback(server.shutdown)
    return server

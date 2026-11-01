"""A run-owned, single-process lost-ACK fixture for the real Oracle endpoint.

Forward authenticated bytes unchanged and drop the first successful reply after
Oracle commits. This is fault injection outside both application processes, not a
second status receiver or selection implementation. Retain exact bodies for the
maintained scenario's immutable-redelivery assertions.
"""

from contextlib import closing
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import signal

from .artifacts import load_json


class LostAckHandler(BaseHTTPRequestHandler):
    """Forward one bounded status request to the configured local Oracle."""

    def do_POST(self):
        """Record forwarded bytes and drop only the first post-commit success."""
        if self.path != "/v1/status-events":
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers["Content-Length"]))
        with closing(
            HTTPConnection("127.0.0.1", self.server.oracle_port, timeout=5)
        ) as connection:
            connection.request("POST", self.path, body, dict(self.headers))
            response = connection.getresponse()
            content = response.read()
            with (self.server.root / "proxy-events.jsonl").open("a") as log:
                log.write(
                    json.dumps({"body": body.decode(), "status": response.status})
                    + "\n"
                )
            marker = self.server.root / "dropped-ack"
            if response.status == 200 and not marker.exists():
                marker.write_text("Dropped the first committed ACK\n")
                self.close_connection = True
                return
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in (
                    "connection",
                    "transfer-encoding",
                    "server",
                    "date",
                    "content-length",
                ):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    def do_GET(self):
        """Expose fixture attempt evidence; this is not an Oracle application API."""
        if self.path != "/events":
            self.send_error(404)
            return
        log = self.server.root / "proxy-events.jsonl"
        events = (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        body = json.dumps(events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Keep expected retry/disconnection noise in the evidence journal only."""


def serve(root: Path) -> None:
    """Own the fixture's loopback listener until its supervisor stops this session."""
    configuration = load_json(root / "run.json")
    with HTTPServer(
        ("127.0.0.1", configuration["proxyPort"]), LostAckHandler
    ) as server:
        server.root = root
        server.oracle_port = configuration["oraclePort"]
        server.serve_forever(poll_interval=0.05)


def shutdown(signum, frame):
    """Unwind the HTTPServer context instead of leaving an inherited listener."""
    raise KeyboardInterrupt


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, shutdown)
    try:
        serve(args.root)
    except KeyboardInterrupt:
        pass

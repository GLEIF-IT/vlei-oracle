"""Bounded HIO requests for the public two-service qualification."""

from hio.core import http


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

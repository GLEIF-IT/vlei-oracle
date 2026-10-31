"""Yield for one JSON-RPC response on a caller-owned HIO client."""

import json

from hio.core import http


class RpcClientDoer(http.ClientDoer):
    """Deliver expected socket failures to the waiting RPC; HIO exit closes sockets."""

    def recur(self, tyme):
        """Keep a failed connection from unwinding unrelated service tasks."""
        try:
            return super().recur(tyme)
        except OSError as error:
            self.client.responses.append({"errored": True, "error": str(error)})
            return True


def request_rpc(client, *, request_id, method, params, tymth, timeout, tock):
    """Bound the wait and reject mismatched, failed, or unusable responses."""
    client.request(
        method="POST",
        data={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    )
    deadline = tymth() + timeout
    while not client.responses:
        if tymth() >= deadline:
            raise TimeoutError(f"{method} timed out")
        yield tock
    response = client.responses.popleft()
    if response.get("errored"):
        raise OSError(f"{method} transport failed: {response['error']}")
    if response["status"] != 200:
        raise ValueError(f"{method} HTTP status {response['status']}")
    payload = json.loads(response["body"])
    if payload.get("id") != request_id or "error" in payload:
        raise ValueError(f"{method} returned an invalid RPC response")
    return payload["result"]

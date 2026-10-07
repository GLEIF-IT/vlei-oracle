"""HIO request/cue flow for EOA proof and account verification."""

import json

from hio.base import doing
from hio.core import http
from hio.help import decking

from .eoa import verify_eoa_account, verify_eoa_proof


class EoaVerifierDoer(doing.DoDoer):
    """Consume association ACDCs from ``msgs`` and emit completion ``cues``.

    Each cue identifies the association by ``said`` and has a ``kin`` of
    ``verified`` (with result fields), ``failed`` (invalid proof/account), or
    ``retry`` (RPC unavailable or unusable). Only the coordinator persists state.
    One request runs at a time; HTTP clients are owned and closed by this worker.
    """

    def __init__(self, *, rpc_url, timeout=5.0, **kwa):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.msgs = decking.Deck()
        self.cues = decking.Deck()
        super().__init__(doers=[doing.doify(self.verify_do)], **kwa)

    def verify_do(self, tymth, tock=0.0, **opts):
        """Check the proof, yield for RPC responses, then emit one result cue."""
        yield tock
        while True:
            if not self.msgs:
                yield self.tock
                continue
            association = self.msgs.popleft()
            said = association["d"]
            try:
                verify_eoa_proof(association)
            except ValueError as exc:
                self.cues.append({"kin": "failed", "said": said, "error": str(exc)})
                continue

            client_doer = None
            try:
                # A new connection per operation prevents late timed-out responses
                # from being consumed as evidence for the next association.
                client = http.Client(path=self.rpc_url, reconnectable=True)
                client_doer = http.ClientDoer(client=client)
                self.extend([client_doer])
                wallet = association["a"]["extId"].split(":")[2]
                results = []
                for request_id, (method, params) in enumerate(
                    [("eth_chainId", []), ("eth_getCode", [wallet, "latest"])], 1
                ):
                    client.request(
                        method="POST",
                        data={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": method,
                            "params": params,
                        },
                    )
                    deadline = self.tyme + self.timeout
                    while not client.responses:
                        if self.tyme >= deadline:
                            raise TimeoutError(f"{method} timed out")
                        yield self.tock
                    response = client.responses.popleft()
                    if response["status"] != 200:
                        raise ValueError(f"{method} HTTP status {response['status']}")
                    payload = json.loads(response["body"])
                    if payload.get("id") != request_id or "error" in payload:
                        raise ValueError(f"{method} returned an invalid RPC response")
                    results.append(payload["result"])
                chain_id = int(results[0], 16)
                if not results[1].startswith("0x"):
                    raise ValueError("eth_getCode returned invalid hex data")
                code = bytes.fromhex(results[1][2:])
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                # Transport/RPC failures are not evidence of an invalid credential.
                self.cues.append({"kin": "retry", "said": said, "error": str(exc)})
                continue
            finally:
                if client_doer is not None:
                    self.remove([client_doer])

            try:
                result = verify_eoa_account(association, chain_id=chain_id, code=code)
            except ValueError as exc:
                self.cues.append({"kin": "failed", "said": said, "error": str(exc)})
            else:
                self.cues.append({"kin": "verified", "said": said, "verified": result})

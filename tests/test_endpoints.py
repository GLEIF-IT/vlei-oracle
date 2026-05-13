from __future__ import annotations

import unittest

import falcon
from falcon import testing

from vlei_oracle.basing import OracleBaser, STATE_FAILED, STATE_POSTED, STATE_VERIFYING
from vlei_oracle.constants import WALLET_SCHEMA, WALLET_SIGNATURE_TYPE
from vlei_oracle.endpoints import load_ends
from vlei_oracle.wallet import encode_eth_signature_cesr


class EndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baser = OracleBaser(name="test-vlei-oracle", temp=True)
        self.rxbs = bytearray()
        app = falcon.App()
        load_ends(app, baser=self.baser, rxbs=self.rxbs, mbx=None)
        self.client = testing.TestClient(app)
        self.signature = "0x" + "11" * 65
        self.cesr_signature = encode_eth_signature_cesr(self.signature)

    def tearDown(self) -> None:
        self.baser.close(clear=True)

    def test_signature_post_completes_pending_operation(self) -> None:
        self.baser.start_operation(
            wallet_said="EWALLET",
            grant_said="EGRANT",
            grant_sender="EOOR",
            wallet_schema=WALLET_SCHEMA,
        )

        response = self.client.simulate_post("/presentations/EWALLET/signature", json=self.signature_body())

        self.assertEqual(response.status_code, 202)
        op = self.baser.get_operation("EWALLET")
        self.assertEqual(op.state, STATE_VERIFYING)
        self.assertEqual(op.signature, self.signature)
        self.assertEqual(op.chainId, 31337)

    def test_signature_before_grant_returns_404(self) -> None:
        response = self.client.simulate_post("/presentations/EWALLET/signature", json=self.signature_body())

        self.assertEqual(response.status_code, 404)

    def test_duplicate_posted_signature_is_idempotent(self) -> None:
        self.baser.start_operation(
            wallet_said="EWALLET",
            grant_said="EGRANT",
            grant_sender="EOOR",
            wallet_schema=WALLET_SCHEMA,
        )
        op = self.baser.attach_signature(
            wallet_said="EWALLET",
            signature_type=WALLET_SIGNATURE_TYPE,
            signature=self.signature,
            wallet="0x1000000000000000000000000000000000000001",
            chain_id=31337,
        )
        op.state = STATE_POSTED
        self.baser.pin_operation(op)

        response = self.client.simulate_post("/presentations/EWALLET/signature", json=self.signature_body())

        self.assertEqual(response.status_code, 202)

    def test_failed_operation_rejects_signature_post(self) -> None:
        op = self.baser.start_operation(
            wallet_said="EWALLET",
            grant_said="EGRANT",
            grant_sender="EOOR",
            wallet_schema=WALLET_SCHEMA,
        )
        self.baser.mark_failed(op, "bad grant")

        response = self.client.simulate_post("/presentations/EWALLET/signature", json=self.signature_body())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.baser.get_operation("EWALLET").state, STATE_FAILED)

    def test_invalid_cesr_signature_returns_400(self) -> None:
        self.baser.start_operation(
            wallet_said="EWALLET",
            grant_said="EGRANT",
            grant_sender="EOOR",
            wallet_schema=WALLET_SCHEMA,
        )
        body = self.signature_body()
        body["signature"] = "not-cesr"

        response = self.client.simulate_post("/presentations/EWALLET/signature", json=body)

        self.assertEqual(response.status_code, 400)

    def test_health_reports_operation_counts(self) -> None:
        self.baser.start_operation(
            wallet_said="EWALLET",
            grant_said="EGRANT",
            grant_sender="EOOR",
            wallet_schema=WALLET_SCHEMA,
        )

        response = self.client.simulate_get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["operations"]["awaiting_signature"], 1)

    def signature_body(self) -> dict:
        return {
            "signatureType": WALLET_SIGNATURE_TYPE,
            "signature": self.cesr_signature,
            "wallet": "0x1000000000000000000000000000000000000001",
            "chainId": 31337,
        }


if __name__ == "__main__":
    unittest.main()

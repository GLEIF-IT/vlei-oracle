from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from vlei_oracle.basing import OracleBaser, OperationRecord, STATE_AWAITING_ARTIFACTS, STATE_AWAITING_SIGNATURE
from vlei_oracle.constants import WALLET_SCHEMA, WALLET_SIGNATURE_TYPE
from vlei_oracle.handling import GrantNotificationDoer, PresentationVerifierDoer
from vlei_oracle.keri_store import MissingCredentialArtifactsError


class HandlingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baser = OracleBaser(name="test-vlei-oracle-handling", temp=True)

    def tearDown(self) -> None:
        self.baser.close(clear=True)

    def test_grant_notification_creates_pending_operation_without_admit(self) -> None:
        exn = Mock()
        exn.ked = {"e": {"acdc": {"d": "EWALLET", "i": "EOOR", "s": WALLET_SCHEMA}}}
        doer = GrantNotificationDoer(
            baser=self.baser,
            hby=Mock(),
            notifier=Mock(),
            parser=Mock(),
            expected_wallet_schema=WALLET_SCHEMA,
        )

        with patch("vlei_oracle.handling.exchanging.cloneMessage", return_value=(exn, {})):
            op = doer.process_grant_notice({"d": "EGRANT"})

        self.assertEqual(op.walletCredentialSaid, "EWALLET")
        self.assertEqual(op.grantSaid, "EGRANT")
        self.assertEqual(op.state, STATE_AWAITING_SIGNATURE)

    def test_missing_artifacts_remain_non_terminal(self) -> None:
        op = OperationRecord.new(
            wallet_said="EWALLET",
            grant_said="EGRANT",
            grant_sender="EOOR",
            wallet_schema=WALLET_SCHEMA,
        )
        op.signatureType = WALLET_SIGNATURE_TYPE
        op.signature = "0x" + "11" * 65
        op.state = "verifying"
        self.baser.pin_operation(op)
        doer = PresentationVerifierDoer(
            baser=self.baser,
            rgy=Mock(),
            trusted_geda_aid="EGEDA",
        )

        with patch(
            "vlei_oracle.handling.presentation_from_reger",
            side_effect=MissingCredentialArtifactsError("credential EROOT is not available yet"),
        ):
            result = doer.verify_operation(op)

        self.assertEqual(result.state, STATE_AWAITING_ARTIFACTS)
        self.assertIn("not available yet", result.failureReason)


if __name__ == "__main__":
    unittest.main()

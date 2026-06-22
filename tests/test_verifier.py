from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Callable

from eth_account import Account
from eth_account.messages import encode_typed_data
from keri.core import coring

from vlei_oracle.constants import (
    LE_SCHEMA,
    OOR_AUTH_SCHEMA,
    OOR_SCHEMA,
    QVI_SCHEMA,
    WALLET_EDGE_OPERATOR,
    WALLET_SCHEMA,
    WALLET_SIGNATURE_TYPE,
)
from vlei_oracle.keri_acdc import saidify_sad
from vlei_oracle.verifier import VerificationError, verify_presentation
from vlei_oracle.wallet import (
    build_typed_data,
    decode_eth_signature_cesr,
    encode_eth_signature_cesr,
    wallet_attributes_from_typed_data,
)


GEDA_AID = "EGEDA"
QVI_AID = "EQVI"
LE_AID = "ELE"
OOR_AID = "EOOR"
LEI = "5493001KJTIIGC8Y1R12"

USDC_TOKEN = "0x1000000000000000000000000000000000000001"
WRAPPER = "0x1000000000000000000000000000000000000002"
IDENTITY_REGISTRY = "0x1000000000000000000000000000000000000003"
CREDENTIAL_REGISTRY = "0x1000000000000000000000000000000000000004"
ORACLE_REGISTRY = "0x1000000000000000000000000000000000000005"

TEST_PRIVATE_KEY = "0x59c6995e998f97a5a0044966f094538e7f280b5f0711c98fc821f4b3ea0f965d"


class VerifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wallet = Account.from_key(TEST_PRIVATE_KEY).address

    def test_valid_wallet_attestation_verifies(self) -> None:
        presentation, signature = self.presentation()
        wallet_credential = presentation["credentials"][presentation["walletCredential"]]

        result = self.verify(presentation, signature)

        self.assertEqual(result.legal_entity_aid, LE_AID)
        self.assertEqual(result.lei, LEI)
        self.assertEqual(result.wallet, self.wallet)
        self.assertEqual(result.le_credential_said, wallet_credential["e"]["le"]["n"])
        self.assertEqual(result.oor_credential_said, wallet_credential["e"]["oor"]["n"])

    def test_wallet_attestation_verifies_with_cesr_decoded_external_signature(self) -> None:
        presentation, signature = self.presentation()
        cesr_signature = encode_eth_signature_cesr(signature)

        result = verify_presentation(
            presentation,
            trusted_geda_aid=GEDA_AID,
            now=1_700_000_000,
            wallet_signature=decode_eth_signature_cesr(cesr_signature),
            wallet_signature_type=WALLET_SIGNATURE_TYPE,
        )

        self.assertEqual(result.wallet_schema, WALLET_SCHEMA)
        self.assertEqual(result.wallet, self.wallet)

    def test_wallet_attestation_requires_external_signature(self) -> None:
        presentation, _ = self.presentation()

        with self.assertRaisesRegex(VerificationError, "external wallet signature"):
            verify_presentation(
                presentation,
                trusted_geda_aid=GEDA_AID,
                now=1_700_000_000,
            )

    def test_wallet_attestation_rejects_embedded_signature(self) -> None:
        presentation, signature = self.presentation(include_wallet_signature=True)

        with self.assertRaisesRegex(VerificationError, "must not embed"):
            verify_presentation(
                presentation,
                trusted_geda_aid=GEDA_AID,
                now=1_700_000_000,
                wallet_signature=signature,
                wallet_signature_type=WALLET_SIGNATURE_TYPE,
            )

    def test_cesr_signature_round_trip(self) -> None:
        signature = "0x" + "11" * 65
        encoded = encode_eth_signature_cesr(signature)

        self.assertTrue(encoded.startswith("5B"))
        self.assertEqual(decode_eth_signature_cesr(encoded), signature)
        with self.assertRaisesRegex(ValueError, "65 bytes"):
            encode_eth_signature_cesr("0x" + "11" * 64)
        oversized_code = coring.Matter(raw=bytes.fromhex(signature[2:]), code=coring.MtrDex.Bytes_Big_L1).qb64
        with self.assertRaisesRegex(ValueError, "5B"):
            decode_eth_signature_cesr(oversized_code)

    def test_bad_wallet_signature_fails(self) -> None:
        presentation, _ = self.presentation()

        with self.assertRaisesRegex(VerificationError, "wallet signature"):
            self.verify(presentation, "0x" + "00" * 65)

    def test_lei_mismatch_fails(self) -> None:
        presentation, signature = self.presentation()
        self.resaidify_wallet(
            presentation,
            lambda credential: credential["a"].update({"LEI": "54930084UKLVMY22DS16"}),
        )

        with self.assertRaisesRegex(VerificationError, "LEI"):
            self.verify(presentation, signature)

    def test_revoked_credential_fails(self) -> None:
        presentation, signature = self.presentation()
        wallet_credential = presentation["credentials"][presentation["walletCredential"]]
        presentation["revokedCredentials"] = [wallet_credential["e"]["oor"]["n"]]

        with self.assertRaisesRegex(VerificationError, "revoked"):
            self.verify(presentation, signature)

    def test_expired_wallet_attestation_fails(self) -> None:
        presentation, signature = self.presentation(expires_at=1_600_000_000)

        with self.assertRaisesRegex(VerificationError, "expired"):
            self.verify(presentation, signature)

    def test_wrong_deployment_domain_fails_signature(self) -> None:
        presentation, signature = self.presentation()
        self.resaidify_wallet(
            presentation,
            lambda credential: credential["a"].update(
                {"oracleRegistry": "0x1000000000000000000000000000000000000099"}
            ),
        )

        with self.assertRaisesRegex(VerificationError, "wallet signature"):
            self.verify(presentation, signature)

    def test_wallet_edge_operator_must_be_ni2i(self) -> None:
        presentation, signature = self.presentation()
        self.resaidify_wallet(
            presentation,
            lambda credential: credential["e"]["le"].update({"o": "I2I"}),
        )

        with self.assertRaisesRegex(VerificationError, "operator"):
            self.verify(presentation, signature)

    def test_tampered_wallet_credential_said_fails_before_signature(self) -> None:
        presentation, signature = self.presentation()
        presentation["credentials"][presentation["walletCredential"]]["a"]["nonce"] = "tampered"

        with self.assertRaisesRegex(VerificationError, "SAID"):
            self.verify(presentation, signature)

    def presentation(
        self,
        *,
        expires_at: int = 1_800_000_000,
        include_wallet_signature: bool = False,
    ) -> tuple[dict, str]:
        return self.presentation_with_signature(
            expires_at=expires_at,
            include_wallet_signature=include_wallet_signature,
        )

    def presentation_with_signature(
        self,
        *,
        expires_at: int = 1_800_000_000,
        include_wallet_signature: bool = False,
    ) -> tuple[dict, str]:
        credentials, wallet_said, signature = self.presentation_credentials(
            expires_at=expires_at,
            include_wallet_signature=include_wallet_signature,
        )
        return {
            "walletCredential": wallet_said,
            "credentials": credentials,
            "revokedCredentials": [],
        }, signature

    def presentation_credentials(
        self,
        *,
        expires_at: int = 1_800_000_000,
        include_wallet_signature: bool = False,
    ) -> tuple[dict, str, str]:
        qvi_credential = saidify_sad(
            {
                "d": "",
                "i": GEDA_AID,
                "s": QVI_SCHEMA,
                "a": {"i": QVI_AID, "LEI": LEI},
            }
        )
        qvi_said = qvi_credential["d"]

        le_credential = saidify_sad(
            {
                "d": "",
                "i": QVI_AID,
                "s": LE_SCHEMA,
                "a": {"i": LE_AID, "LEI": LEI},
                "e": {"qvi": {"n": qvi_said, "s": QVI_SCHEMA}},
            }
        )
        le_said = le_credential["d"]

        auth_credential = saidify_sad(
            {
                "d": "",
                "i": LE_AID,
                "s": OOR_AUTH_SCHEMA,
                "a": {
                    "AID": OOR_AID,
                    "LEI": LEI,
                    "personLegalName": "Pat Example",
                    "officialRole": "Chief Compliance Officer",
                },
                "e": {"le": {"n": le_said, "s": LE_SCHEMA}},
            }
        )
        auth_said = auth_credential["d"]

        oor_credential = saidify_sad(
            {
                "d": "",
                "i": QVI_AID,
                "s": OOR_SCHEMA,
                "a": {
                    "i": OOR_AID,
                    "LEI": LEI,
                    "personLegalName": "Pat Example",
                    "officialRole": "Chief Compliance Officer",
                },
                "e": {"auth": {"n": auth_said, "s": OOR_AUTH_SCHEMA}},
            }
        )
        oor_said = oor_credential["d"]

        typed_data = build_typed_data(
            legal_entity_aid=LE_AID,
            lei=LEI,
            oor_aid=OOR_AID,
            le_credential_said=le_said,
            oor_credential_said=oor_said,
            wallet=self.wallet,
            chain_id=31337,
            usdc_token=USDC_TOKEN,
            wrapper=WRAPPER,
            identity_registry=IDENTITY_REGISTRY,
            credential_registry=CREDENTIAL_REGISTRY,
            oracle_registry=ORACLE_REGISTRY,
            nonce="nonce-1",
            expires_at=expires_at,
            purpose="GL1_WUSDC_TRANSFER",
        )
        signature = self.sign_typed_data(typed_data)
        wallet_attrs = wallet_attributes_from_typed_data(typed_data=typed_data)
        if include_wallet_signature:
            wallet_attrs["walletSignatureType"] = WALLET_SIGNATURE_TYPE
            wallet_attrs["walletSignature"] = signature
        wallet_credential = saidify_sad(
            {
                "d": "",
                "i": OOR_AID,
                "s": WALLET_SCHEMA,
                "a": wallet_attrs,
                "e": {
                    "le": {"n": le_said, "s": LE_SCHEMA, "o": WALLET_EDGE_OPERATOR},
                    "oor": {"n": oor_said, "s": OOR_SCHEMA, "o": WALLET_EDGE_OPERATOR},
                },
            }
        )
        wallet_said = wallet_credential["d"]

        credentials = {
            qvi_said: qvi_credential,
            le_said: le_credential,
            auth_said: auth_credential,
            oor_said: oor_credential,
            wallet_said: wallet_credential,
        }
        return credentials, wallet_said, signature

    def resaidify_wallet(self, presentation: dict, mutator: Callable[[dict], None]) -> None:
        old_said = presentation["walletCredential"]
        credential = deepcopy(presentation["credentials"].pop(old_said))
        mutator(credential)
        credential["d"] = ""
        new_credential = saidify_sad(credential)
        presentation["walletCredential"] = new_credential["d"]
        presentation["credentials"][new_credential["d"]] = new_credential

    def sign_typed_data(self, typed_data: dict) -> str:
        signable = encode_typed_data(full_message=typed_data)
        return Account.sign_message(signable, TEST_PRIVATE_KEY).signature.hex()

    def verify(self, presentation: dict, signature: str):
        return verify_presentation(
            presentation,
            trusted_geda_aid=GEDA_AID,
            now=1_700_000_000,
            wallet_signature=signature,
            wallet_signature_type=WALLET_SIGNATURE_TYPE,
        )


if __name__ == "__main__":
    unittest.main()

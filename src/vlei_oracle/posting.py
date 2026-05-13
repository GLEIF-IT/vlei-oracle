"""Web3-backed posting of verified vLEI Oracle evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_account import Account

from .constants import WALLET_CREDENTIAL_TYPE
from .verifier import VerifiedWalletBinding


ZERO_BYTES32 = "0x" + "00" * 32


IDENTITY_REGISTRY_ABI = [
    {
        "type": "function",
        "name": "getIdentity",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "registerIdentity",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "ccid", "type": "bytes32"},
            {"name": "account", "type": "address"},
            {"name": "context", "type": "bytes"},
        ],
        "outputs": [],
    },
]


CREDENTIAL_REGISTRY_ABI = [
    {
        "type": "function",
        "name": "getCredential",
        "stateMutability": "view",
        "inputs": [
            {"name": "ccid", "type": "bytes32"},
            {"name": "credentialTypeId", "type": "bytes32"},
        ],
        "outputs": [
            {
                "components": [
                    {"name": "expiresAt", "type": "uint40"},
                    {"name": "credentialData", "type": "bytes"},
                ],
                "name": "",
                "type": "tuple",
            }
        ],
    },
    {
        "type": "function",
        "name": "registerCredential",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "ccid", "type": "bytes32"},
            {"name": "credentialTypeId", "type": "bytes32"},
            {"name": "expiresAt", "type": "uint40"},
            {"name": "credentialData", "type": "bytes"},
            {"name": "context", "type": "bytes"},
        ],
        "outputs": [],
    },
]


ORACLE_REGISTRY_ABI = [
    {
        "type": "function",
        "name": "getLegalEntityRecord",
        "stateMutability": "view",
        "inputs": [{"name": "aidCcid", "type": "bytes32"}],
        "outputs": [
            {
                "components": [
                    {"name": "aid", "type": "string"},
                    {"name": "entityLei", "type": "string"},
                    {"name": "entityLeiHash", "type": "bytes32"},
                    {"name": "credentialSubjectLeiHash", "type": "bytes32"},
                    {"name": "acdcSaidHash", "type": "bytes32"},
                    {"name": "proofHash", "type": "bytes32"},
                    {"name": "issuedAt", "type": "uint40"},
                    {"name": "expiresAt", "type": "uint40"},
                    {"name": "exists", "type": "bool"},
                    {"name": "allowed", "type": "bool"},
                    {"name": "revoked", "type": "bool"},
                ],
                "name": "",
                "type": "tuple",
            }
        ],
    },
    {
        "type": "function",
        "name": "postLegalEntityRecord",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "aid", "type": "string"},
            {"name": "entityLei", "type": "string"},
            {"name": "credentialSubjectLei", "type": "string"},
            {"name": "acdcSaidHash", "type": "bytes32"},
            {"name": "proofHash", "type": "bytes32"},
            {"name": "issuedAt", "type": "uint40"},
            {"name": "expiresAt", "type": "uint40"},
            {"name": "allowed", "type": "bool"},
            {"name": "revoked", "type": "bool"},
        ],
        "outputs": [{"name": "aidCcid", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "setCredentialRevoked",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "aidCcid", "type": "bytes32"},
            {"name": "revoked", "type": "bool"},
        ],
        "outputs": [],
    },
]


@dataclass(frozen=True)
class PostAction:
    """One idempotent contract call that still needs to be submitted."""

    name: str
    fn: Any


@dataclass(frozen=True)
class TransactionReceipt:
    """Minimal receipt shape consumed by the HIO posting Doer."""

    tx_hash: str
    block_number: int
    status: int


class Web3ContractPoster:
    """Idempotent poster for vlei-comply identity and credential registries."""

    def __init__(
        self,
        *,
        contracts: dict[str, str],
        rpc_url: str,
        private_key: str,
        gas_multiplier: float = 1.2,
    ):
        try:
            from web3 import Web3
            from web3.exceptions import TransactionNotFound
        except ImportError as exc:
            raise RuntimeError("web3 is required for vlei-oracle service posting") from exc

        self.Web3 = Web3
        self.TransactionNotFound = TransactionNotFound
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise RuntimeError(f"unable to connect to RPC URL {rpc_url}")
        self.account = Account.from_key(private_key)
        self.gas_multiplier = gas_multiplier
        self.identity = self.w3.eth.contract(
            address=self.w3.to_checksum_address(contracts["identityRegistry"]),
            abi=IDENTITY_REGISTRY_ABI,
        )
        self.credentials = self.w3.eth.contract(
            address=self.w3.to_checksum_address(contracts["credentialRegistry"]),
            abi=CREDENTIAL_REGISTRY_ABI,
        )
        self.oracle = self.w3.eth.contract(
            address=self.w3.to_checksum_address(contracts["oracleRegistry"]),
            abi=ORACLE_REGISTRY_ABI,
        )

    def plan_verified_binding(
        self,
        binding: VerifiedWalletBinding,
        *,
        initial_aid_allowed: bool = False,
    ) -> list[PostAction]:
        """Return the contract calls still needed for this binding.

        This is the Web3 edge adapter. It may perform bounded synchronous RPC
        reads, but it never waits, sleeps, or owns retry timing; HIO Doers own
        all waiting and flow control around it.
        """

        actions: list[PostAction] = []
        aid_ccid = aid_ccid_for(binding)
        credential_type_id = wallet_credential_type_id(binding)
        proof = proof_hash(binding)
        le_said_hash = keccak_text(binding.le_credential_said)

        current_ccid = self.identity.functions.getIdentity(binding.wallet).call()
        if _hexbytes(current_ccid) == ZERO_BYTES32:
            actions.append(
                PostAction(
                    name="registerIdentity",
                    fn=self.identity.functions.registerIdentity(aid_ccid, binding.wallet, b""),
                )
            )
        elif _hexbytes(current_ccid).lower() != aid_ccid.lower():
            raise RuntimeError(f"wallet {binding.wallet} is already registered to {_hexbytes(current_ccid)}")

        try:
            current = self.credentials.functions.getCredential(aid_ccid, credential_type_id).call()
        except Exception:
            current = None
        if _credential_is_empty(current):
            actions.append(
                PostAction(
                    name="registerCredential",
                    fn=self.credentials.functions.registerCredential(
                        aid_ccid,
                        credential_type_id,
                        binding.expires_at,
                        bytes.fromhex(proof[2:]),
                        b"",
                    ),
                )
            )

        record = self.oracle.functions.getLegalEntityRecord(aid_ccid).call()
        if not _record_matches(
            record,
            binding=binding,
            le_said_hash=le_said_hash,
            proof=proof,
            initial_aid_allowed=initial_aid_allowed,
        ):
            actions.append(
                PostAction(
                    name="postLegalEntityRecord",
                    fn=self.oracle.functions.postLegalEntityRecord(
                        binding.legal_entity_aid,
                        binding.lei,
                        binding.lei,
                        le_said_hash,
                        proof,
                        binding.issued_at,
                        binding.expires_at,
                        initial_aid_allowed,
                        False,
                    ),
                )
            )

        return actions

    def submit(self, action: PostAction) -> str:
        """Submit one planned transaction without waiting for a receipt."""

        tx = action.fn.build_transaction(
            {
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "chainId": self.w3.eth.chain_id,
            }
        )
        if "gas" not in tx:
            estimated = self.w3.eth.estimate_gas(tx)
            tx["gas"] = max(21_000, int(estimated * self.gas_multiplier))
        if "gasPrice" not in tx and "maxFeePerGas" not in tx:
            tx["gasPrice"] = self.w3.eth.gas_price

        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", getattr(signed, "rawTransaction", None))
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        return tx_hash.hex()

    def receipt(self, tx_hash: str) -> TransactionReceipt | None:
        """Read a receipt if available; return None while the tx is pending."""

        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except self.TransactionNotFound:
            return None
        return TransactionReceipt(
            tx_hash=tx_hash,
            block_number=int(receipt.blockNumber),
            status=int(receipt.status),
        )

    def current_block(self) -> int:
        return int(self.w3.eth.block_number)


def keccak_text(value: str) -> str:
    from web3 import Web3

    return _with_0x(Web3.keccak(text=value).hex())


def aid_ccid_for(binding: VerifiedWalletBinding) -> str:
    return keccak_text(binding.legal_entity_aid)


def wallet_credential_type_id(binding: VerifiedWalletBinding) -> str:
    material = f"{WALLET_CREDENTIAL_TYPE}:{binding.chain_id}:{binding.wallet.lower()}"
    return keccak_text(material)


def proof_hash(binding: VerifiedWalletBinding) -> str:
    material = (
        f"{binding.wallet_credential_said}:"
        f"{binding.le_credential_said}:"
        f"{binding.oor_credential_said}:"
        f"{binding.wallet.lower()}:"
        f"{binding.nonce}"
    )
    return keccak_text(material)


def _hexbytes(value: Any) -> str:
    if isinstance(value, str):
        return _with_0x(value)
    return "0x" + bytes(value).hex()


def _credential_is_empty(current: Any) -> bool:
    if current is None:
        return True
    if isinstance(current, (list, tuple)) and len(current) >= 2:
        return int(current[0]) == 0 and len(bytes(current[1])) == 0
    return False


def _with_0x(value: str) -> str:
    return value if value.startswith("0x") else f"0x{value}"


def _record_matches(
    record: Any,
    *,
    binding: VerifiedWalletBinding,
    le_said_hash: str,
    proof: str,
    initial_aid_allowed: bool,
) -> bool:
    if not record or not bool(record[8]):
        return False
    return (
        record[0] == binding.legal_entity_aid
        and record[1] == binding.lei
        and _hexbytes(record[4]).lower() == le_said_hash.lower()
        and _hexbytes(record[5]).lower() == proof.lower()
        and int(record[7]) == binding.expires_at
        and bool(record[9]) == initial_aid_allowed
        and not bool(record[10])
    )

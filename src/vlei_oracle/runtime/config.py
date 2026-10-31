"""Validated oracle identity, trust and posting configuration."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from keri.core import coring


@dataclass(frozen=True)
class ReceiverConfig:
    """One trusted verifier, exact public destination URL, and persistent identity."""

    verifier_aid: str
    chain_id: int
    webhook_url: str
    verifier_kel: str = ""
    name: str = "vlei-oracle-inbox"
    base: str = ""
    alias: str = "oracle"
    passcode: str | None = field(default=None, repr=False)
    incept_file: str | None = None
    head_dir: str | None = None
    host: str = "0.0.0.0"
    http_port: int = 9723
    temp: bool = False
    rpc_url: str = ""
    endorsement_registry: str = ""
    identity_registry: str = ""
    posting_key: str = field(default="", repr=False)
    confirmations: int = 2
    posting_interval: float = 1.0

    def __post_init__(self):
        """Reject invalid operational configuration before opening stores."""
        if self.rpc_url:
            from eth_account import Account
            from web3 import Web3

            Account.from_key(self.posting_key)
            for address in (self.endorsement_registry, self.identity_registry):
                if not Web3.is_address(address):
                    raise ValueError("posting requires both registry addresses")
            if self.confirmations < 1 or self.posting_interval <= 0:
                raise ValueError("confirmations and posting interval must be positive")
        parts = urlsplit(self.webhook_url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.netloc
            or parts.fragment
            or parts.path != "/v1/status-events"
            or parts.username
            or parts.password
        ):
            raise ValueError(
                "webhook URL must be an absolute HTTP(S) /v1/status-events URL"
            )
        if type(self.chain_id) is not int or self.chain_id <= 0:
            raise ValueError("positive chain ID is required")
        if coring.Prefixer(qb64=self.verifier_aid).qb64 != self.verifier_aid:
            raise ValueError("canonical verifier AID is required")

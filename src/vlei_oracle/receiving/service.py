"""Trusted-status HIO service with optional contract posting and signed ACKs."""

from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import falcon
from hio.base import doing
from hio.core import http
from keri.app import configing, habbing, keeping
from keri.core import coring
from keri.db import basing, dbing
from keri.end import ending

from vlei_oracle.authing import ServiceAuthenticator
from vlei_oracle.basing import InboxScope, OracleBaser
from vlei_oracle.identity import incept_if_new
from vlei_oracle.receiving.status import StatusEnd


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
    host: str = "127.0.0.1"
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
        if not ip_address(self.host).is_loopback:
            raise ValueError("status receiver must bind a loopback IP behind bounded ingress")
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


class StatusReceiver(doing.DoDoer):
    """Borrow identity and stores; let the HIO HTTP Doer own its listener.

    Peer KEL import uses KERIpy validation. Only accepted local key state can
    authorize a webhook; no header-provided key or credential verifier is used.
    Update the KEL file and restart after peer rotation.
    """

    def __init__(self, config, *, hby, baser):
        self.hab = incept_if_new(
            hby=hby, alias=config.alias, incept_file=config.incept_file
        )
        if config.verifier_kel:
            hby.psr.parse(ims=bytearray(Path(config.verifier_kel).read_bytes()))
        if config.verifier_aid not in hby.kevers:
            raise ValueError("verifier key state unavailable; supply --verifier-kel")
        baser.bind_inbox_scope(InboxScope(config.verifier_aid, config.chain_id))
        auth = ServiceAuthenticator(
            hab=self.hab, baser=baser, peer_aid=config.verifier_aid
        )
        self.app = falcon.App()
        self.app.add_route(
            "/v1/status-events",
            StatusEnd(
                auth=auth,
                baser=baser,
                target=config.webhook_url,
                chain_id=config.chain_id,
            ),
        )
        # KERIpy OOBI export lets the verifier learn this receiver's signing AID.
        self.app.add_route("/oobi/{aid}", ending.OOBIEnd(hby=hby))
        self.server = http.Server(host=config.host, port=config.http_port, app=self.app)
        doers = [http.ServerDoer(server=self.server)]
        if config.rpc_url:
            from eth_account import Account
            from vlei_oracle.endorsement.effects import ContractEffects
            from vlei_oracle.endorsement.posting import StatusPoster

            effects = ContractEffects(
                rpc_url=config.rpc_url, chain_id=config.chain_id,
                writer=Account.from_key(config.posting_key).address,
                endorsement=config.endorsement_registry, identity=config.identity_registry,
                confirmations=config.confirmations,
            )
            doers.append(StatusPoster(
                baser=baser, effects=effects, key=config.posting_key,
                interval=config.posting_interval,
            ))
        super().__init__(doers=doers)


def run_receiver(config, *, expire=0.0):
    """Run HIO inside KERIpy store contexts; child sockets close before stores.

    temp is explicit for every context. Persistent stores survive shutdown; an
    exception during later startup closes previously acquired stores. Supplied
    KERIpy stores are borrowed by Habery, with no second cleanup-owning Doer.
    """
    options = dict(name=config.name, base=config.base, headDirPath=config.head_dir)
    with (
        keeping.openKS(temp=config.temp, **options) as ks,
        basing.openDB(temp=config.temp, **options) as db,
        configing.openCF(temp=config.temp, **options) as cf,
        dbing.openLMDB(cls=OracleBaser, temp=config.temp, **options) as baser,
    ):
        hby = habbing.Habery(
            name=config.name,
            base=config.base,
            temp=config.temp,
            ks=ks,
            db=db,
            cf=cf,
            bran=config.passcode,
            free=False,
        )
        receiver = StatusReceiver(config, hby=hby, baser=baser)
        print(
            f"vlei-oracle receiving as {receiver.hab.pre} on {config.host}:{config.http_port}",
            flush=True,
        )
        doing.Doist(real=True, tock=0.03125, limit=expire).do(doers=[receiver])

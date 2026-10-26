"""Receive-only HIO service: native service identity, durable inbox, signed ACKs."""

from dataclasses import dataclass, field
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
    host: str = "0.0.0.0"
    http_port: int = 9723
    temp: bool = False

    def __post_init__(self):
        """Reject invalid operational configuration before opening stores."""
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
    """Borrow identity and stores; let the native HTTP Doer own its listener.

    Peer KEL import uses native validation. Only accepted local key state can
    authorize a webhook; no header-provided key, credential verifier or posting
    worker is installed. Update the KEL file and restart after peer rotation.
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
        # Native OOBI export lets the verifier learn this receiver's signing AID.
        self.app.add_route("/oobi/{aid}", ending.OOBIEnd(hby=hby))
        self.server = http.Server(host=config.host, port=config.http_port, app=self.app)
        super().__init__(doers=[http.ServerDoer(server=self.server)])


def run_receiver(config, *, expire=0.0):
    """Run HIO inside native store contexts; child sockets close before stores.

    temp is explicit for every context. Persistent stores survive shutdown; an
    exception during later startup closes previously acquired stores. Supplied
    native stores are borrowed by Habery, with no second cleanup-owning Doer.
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

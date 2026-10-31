"""Compose KERI ingestion, association verification, and HTTP HIO tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import falcon
from hio.base import doing
from hio.core import http
from hio.help import decking
from keri.app import habbing, httping, indirecting, notifying, oobiing, storing
from keri.core import eventing, parsing, routing, scheming
from keri.db import basing
from keri.end import ending
from keri.peer import exchanging
from keri.vc import protocoling
from keri.vdr import credentialing, verifying
from keri.vdr.eventing import Tevery

from vlei_oracle.identity import incept_if_new
from vlei_oracle.basing import OracleBaser
from vlei_oracle.endorsement.effects import ContractEffects
from vlei_oracle.endorsement.reconciling import EndorsementDoer
from vlei_oracle.receiving.endpoints import load_ends
from vlei_oracle.receiving.grants import GrantNotificationDoer
from vlei_oracle.receiving.processing import OracleAgent
from vlei_oracle.verification.constants import ASSOCIATION_SCHEMA
from vlei_oracle.verification.coordinating import VerificationCoordinator
from vlei_oracle.verification.schema_cache import pin_accepted_schemas
from vlei_oracle.verification.worker import EoaVerifierDoer


@dataclass(frozen=True)
class ServiceConfig:
    """Configuration for one fresh association-verification service."""

    name: str = "vlei-oracle"
    base: str = ""
    alias: str = "oracle"
    passcode: str | None = None
    http_port: int = 9723
    host: str = "0.0.0.0"
    trusted_geda_aid: str = ""
    rpc_url: str = ""
    no_post: bool = False
    incept_file: str | None = None
    temp: bool = False
    association_schema: str = ""
    schema_dir: str = ""
    chain_id: int = 0
    endorsement_registry: str = ""
    identity_registry: str = ""
    posting_key: str = field(default="", repr=False)
    confirmations: int = 2
    posting_interval: float = 1.0


@dataclass
class ServiceRuntime:
    """Constructed service components exposed for tests and embedding.

    The returned ``doers`` list is intentionally complete. Callers should hand
    it to a HIO ``Doist`` rather than starting individual pieces themselves.
    """

    hby: Any
    hab: Any
    rgy: Any
    baser: OracleBaser
    app: falcon.App
    doers: list[Any]


def setup_service(config: ServiceConfig) -> ServiceRuntime:
    """Build the Habery, parser, Falcon routes, and Doers for the service.

    Ordering matters: schemas are pinned before parser/verifier use, KERI
    handlers are registered before the parser receives traffic, and the Falcon
    direct-mode endpoint receives the parser input bytearray. The service AID is
    created or reopened from the same Habery used by the HTTP and mailbox Doers.
    """

    if not config.rpc_url:
        raise ValueError("association verification requires --rpc-url")
    if not config.no_post and not (
        config.chain_id > 0 and config.endorsement_registry and
        config.identity_registry and config.posting_key
    ):
        raise ValueError("posting requires chain ID, both registries, and a posting key")
    if config.confirmations < 1 or config.posting_interval <= 0:
        raise ValueError("confirmations and posting interval must be positive")
    hby = habbing.Habery(
        name=config.name,
        base=config.base,
        bran=config.passcode,
        temp=config.temp,
        free=True,
    )
    pin_accepted_schemas(hby)
    if config.schema_dir:
        for path in Path(config.schema_dir).glob("*.json"):
            schema = scheming.Schemer(raw=path.read_bytes())
            hby.db.schema.pin(keys=(schema.said,), val=schema)
    association_schema = ASSOCIATION_SCHEMA
    if config.association_schema:
        schema = scheming.Schemer(raw=Path(config.association_schema).read_bytes())
        if schema.said != ASSOCIATION_SCHEMA:
            raise ValueError("association schema differs from the pinned protocol")
        hby.db.schema.pin(keys=(schema.said,), val=schema)
        association_schema = schema.said
    hab = incept_if_new(hby=hby, alias=config.alias, incept_file=config.incept_file)

    hby_doer = habbing.HaberyDoer(habery=hby)
    oobiery = oobiing.Oobiery(hby=hby, clienter=httping.Clienter())
    rgy = credentialing.Regery(
        hby=hby, name=config.name, base=config.base, temp=config.temp
    )
    rgy_doer = credentialing.RegeryDoer(rgy=rgy)
    verifier = verifying.Verifier(hby=hby, reger=rgy.reger)
    baser = OracleBaser(name=config.name, base=config.base, temp=config.temp)

    cues = decking.Deck()
    rvy = routing.Revery(db=hby.db, cues=cues)
    exc = exchanging.Exchanger(hby=hby, handlers=[])
    # Auxiliary stores must share the same namespace as the identity and registry.
    noter = notifying.Noter(name=config.name, base=config.base, temp=config.temp)
    notifier = notifying.Notifier(hby=hby, noter=noter)
    protocoling.loadHandlers(hby=hby, exc=exc, notifier=notifier)

    kvy = eventing.Kevery(db=hby.db, lax=True, local=False, rvy=rvy, cues=cues)
    kvy.registerReplyRoutes(router=rvy.rtr)
    tvy = Tevery(reger=rgy.reger, db=hby.db, local=False, cues=cues)
    tvy.registerReplyRoutes(router=rvy.rtr)
    parser = parsing.Parser(
        framed=True, kvy=kvy, tvy=tvy, rvy=rvy, vry=verifier, exc=exc
    )
    mbx = storing.Mailboxer(name=config.name, base=config.base, temp=config.temp)

    app = falcon.App(cors_enable=True)
    ending.loadEnds(app=app, hby=hby, default=hab.pre)
    oobiing.loadEnds(app=app, hby=hby, prefix="/ext")
    load_ends(app, baser=baser, rxbs=parser.ims, mbx=mbx)

    server = http.Server(host=config.host, port=config.http_port, app=app)
    rep = storing.Respondant(hby=hby, mbx=mbx)
    mailbox_director = indirecting.MailboxDirector(
        hby=hby,
        topics=["/receipt", "/replay", "/credential", "/delegate", "/challenge"],
        verifier=verifier,
        kvy=kvy,
        tvy=tvy,
        exc=exc,
        rep=rep,
        rvy=rvy,
        cues=cues,
    )

    doers = [
        hby_doer,
        basing.BaserDoer(baser=baser),
        basing.BaserDoer(baser=rgy.reger),
        basing.BaserDoer(baser=noter),
        basing.BaserDoer(baser=mbx),
        rgy_doer,
        *oobiery.doers,
        rep,
        mailbox_director,
        http.ServerDoer(server=server),
        OracleAgent(
            parser=parser,
            kvy=kvy,
            tvy=tvy,
            rvy=rvy,
            verifier=verifier,
            exc=exc,
            cues=cues,
        ),
        GrantNotificationDoer(
            baser=baser,
            hby=hby,
            notifier=notifier,
            parser=parser,
            expected_association_schema=association_schema,
        ),
        VerificationCoordinator(
            baser=baser,
            rgy=rgy,
            trusted_geda_aid=config.trusted_geda_aid,
            association_schema=association_schema,
            eoa_verifier=(
                EoaVerifierDoer(rpc_url=config.rpc_url) if association_schema else None
            ),
        ),
    ]
    if not config.no_post:
        from eth_account import Account

        effects = ContractEffects(
            rpc_url=config.rpc_url, chain_id=config.chain_id,
            writer=Account.from_key(config.posting_key).address,
            endorsement=config.endorsement_registry, identity=config.identity_registry,
            confirmations=config.confirmations,
        )
        doers.append(EndorsementDoer(
            baser=baser, rgy=rgy, trusted_geda_aid=config.trusted_geda_aid,
            association_schema=association_schema, effects=effects,
            private_key=config.posting_key, interval=config.posting_interval,
        ))
    return ServiceRuntime(hby=hby, hab=hab, rgy=rgy, baser=baser, app=app, doers=doers)


def run_service(config: ServiceConfig, *, expire: float = 0.0) -> ServiceRuntime:
    """Start the service and block until interrupted or the optional limit expires.

    HIO owns the process clock here. Long-running waits, receipt polling, and
    retry loops should be modeled by Doers that adjust ``tock`` rather than by
    sleeping inside synchronous helpers.
    """

    runtime = setup_service(config)
    print(
        f"vlei-oracle serving {runtime.hab.name}:{runtime.hab.pre} on {config.host}:{config.http_port}"
    )
    doist = doing.Doist(limit=expire, tock=0.03125, real=True)
    doist.do(doers=runtime.doers)
    return runtime

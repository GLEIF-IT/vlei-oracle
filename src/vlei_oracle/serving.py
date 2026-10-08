"""Compose KERI ingestion, association verification, and HTTP HIO tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import falcon
from hio.base import doing
from hio.core import http
from hio.help import decking
from keri.app import habbing, httping, indirecting, notifying, oobiing, storing
from keri.core import eventing, parsing, routing, scheming
from keri.end import ending
from keri.peer import exchanging
from keri.vc import protocoling
from keri.vdr import credentialing, verifying
from keri.vdr.eventing import Tevery

from .basing import OracleBaser
from .constants import ASSOCIATION_SCHEMA
from .contracting import ContractEffects
from .endpoints import load_ends
from .eoa_worker import EoaVerifierDoer
from .handling import GrantNotificationDoer, OracleAgent, PresentationVerifierDoer
from .posting_worker import EndorsementDoer
from .schema_cache import pin_accepted_schemas


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
        PresentationVerifierDoer(
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


def incept_if_new(*, hby, alias: str, incept_file: str | None = None):
    """Create the oracle AID if the Habery does not already contain it.

    The local e2e usually incepts the oracle AID before the service starts, so
    this normally reopens an existing Hab. The fallback keeps standalone server
    starts usable for tests and development.
    """

    hab = hby.habByName(name=alias)
    if hab is not None:
        return hab

    inits = {
        "transferable": True,
        "icount": 1,
        "isith": "1",
        "ncount": 1,
        "nsith": "1",
        "wits": [],
        "toad": 0,
    }
    if incept_file:
        data = json.loads(Path(incept_file).read_text())
        if not isinstance(data, dict):
            raise ValueError("incept_file must contain a JSON object")
        inits.update(data)
    return hby.makeHab(name=alias, **inits)


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

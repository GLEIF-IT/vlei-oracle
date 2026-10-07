"""Assemble KERI ingestion, profile verification, HTTP, and posting HIO doers.

Association proofs arrive inside the credential; the legacy wallet-attestation
mode also accepts a separate REST signature. Schema caches are populated before
KERI processing. Verification and contract posting remain separate transitions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
from .constants import WALLET_SCHEMA
from .endpoints import load_ends
from .eoa_worker import EoaVerifierDoer
from .handling import (
    ContractPosterDoer,
    GrantNotificationDoer,
    OracleAgent,
    PresentationVerifierDoer,
    RevocationMonitorDoer,
)
from .posting import Web3ContractPoster
from .schema_cache import pin_accepted_schemas


@dataclass(frozen=True)
class ServiceConfig:
    """Configuration for one vLEI Oracle service process.

    ``no_post`` is the test/local-verifier escape hatch. Production-like runs
    require contract addresses, an RPC URL, and a posting key so successful
    verification can be committed to the on-chain registries.

    ``association_schema`` selects association mode using a schema JSON path;
    ``schema_dir`` supplies source-credential schemas. This mode requires
    ``no_post`` and an RPC URL for checking the external account.
    """

    name: str = "vlei-oracle"
    base: str = ""
    alias: str = "oracle"
    passcode: str | None = None
    http_port: int = 9723
    host: str = "0.0.0.0"
    trusted_geda_aid: str = ""
    contracts: dict[str, str] | None = None
    rpc_url: str = ""
    private_key: str = ""
    confirmations: int = 0
    no_post: bool = False
    initial_aid_allowed: bool = False
    incept_file: str | None = None
    temp: bool = False
    association_schema: str = ""
    schema_dir: str = ""


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

    if config.association_schema and (not config.no_post or not config.rpc_url):
        raise ValueError("association verification requires --no-post and --rpc-url")
    hby = habbing.Habery(name=config.name, base=config.base, bran=config.passcode, temp=config.temp, free=True)
    pin_accepted_schemas(hby)
    if config.schema_dir:
        for path in Path(config.schema_dir).glob("*.json"):
            schema = scheming.Schemer(raw=path.read_bytes())
            hby.db.schema.pin(keys=(schema.said,), val=schema)
    association_schema = ""
    if config.association_schema:
        schema = scheming.Schemer(raw=Path(config.association_schema).read_bytes())
        hby.db.schema.pin(keys=(schema.said,), val=schema)
        association_schema = schema.said
    hab = incept_if_new(hby=hby, alias=config.alias, incept_file=config.incept_file)

    hby_doer = habbing.HaberyDoer(habery=hby)
    oobiery = oobiing.Oobiery(hby=hby, clienter=httping.Clienter())
    rgy = credentialing.Regery(hby=hby, name=config.name, base=config.base, temp=config.temp)
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
    parser = parsing.Parser(framed=True, kvy=kvy, tvy=tvy, rvy=rvy, vry=verifier, exc=exc)
    mbx = storing.Mailboxer(name=config.name, base=config.base, temp=config.temp)
    oracle_registry = (config.contracts or {}).get("oracleRegistry", "")

    app = falcon.App(cors_enable=True)
    ending.loadEnds(app=app, hby=hby, default=hab.pre)
    oobiing.loadEnds(app=app, hby=hby, prefix="/ext")
    load_ends(app, baser=baser, rxbs=parser.ims, mbx=mbx)

    server = http.Server(host=config.host, port=config.http_port, app=app)
    poster = None
    if not config.no_post:
        if config.contracts is None:
            raise ValueError("contracts are required unless --no-post is set")
        if not config.rpc_url:
            raise ValueError("rpc_url is required unless --no-post is set")
        if not config.private_key:
            raise ValueError("private_key is required unless --no-post is set")
        poster = Web3ContractPoster(
            contracts=config.contracts,
            rpc_url=config.rpc_url,
            private_key=config.private_key,
        )
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
        OracleAgent(parser=parser, kvy=kvy, tvy=tvy, rvy=rvy, verifier=verifier, exc=exc, cues=cues),
        GrantNotificationDoer(
            baser=baser,
            hby=hby,
            notifier=notifier,
            parser=parser,
            expected_wallet_schema=association_schema or WALLET_SCHEMA,
            embedded_proof=bool(association_schema),
        ),
        PresentationVerifierDoer(
            baser=baser,
            rgy=rgy,
            trusted_geda_aid=config.trusted_geda_aid,
            oracle_registry=oracle_registry,
            association_schema=association_schema,
            eoa_verifier=(
                EoaVerifierDoer(rpc_url=config.rpc_url) if association_schema else None
            ),
            no_post=config.no_post,
        ),
        ContractPosterDoer(
            baser=baser,
            poster=poster,
            initial_aid_allowed=config.initial_aid_allowed,
            confirmations=config.confirmations,
        ),
        RevocationMonitorDoer(baser=baser, tvy=tvy),
    ]
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
    print(f"vlei-oracle serving {runtime.hab.name}:{runtime.hab.pre} on {config.host}:{config.http_port}")
    doist = doing.Doist(limit=expire, tock=0.03125, real=True)
    doist.do(doers=runtime.doers)
    return runtime

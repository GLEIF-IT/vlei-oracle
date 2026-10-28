"""Installed verifier/oracle CLIs: witnessed issuance, signed HTTP and real contracts."""

from contextlib import ExitStack
from importlib.resources import files
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from eth_account import Account
from eth_account.messages import encode_typed_data
from hio.base import doing
from keri.app import habbing, httping, indirecting, oobiing
from keri.core import coring, scheming
from keri.db import basing
from keri.help import helping

from ceremony import association_from_challenge
from issuance_helpers import (
    IssuerEscrows,
    anchor,
    approve_qvi,
    grant,
    issue_credential,
    open_issuers,
    receive,
    replay,
    revoke_credential,
    rules,
)
from service_processes import (
    free_port,
    identity,
    launch,
    lost_ack_proxy,
    ready,
    request,
    stop,
)
from test_contract_effects import deployment, effects_for, KEY
from vlei_oracle.verification.constants import LE_SCHEMA, QVI_SCHEMA
from vlei_oracle.verification.eoa import build_challenge


def test_two_installed_services_publish_replace_and_retire(tmp_path, deployment):
    """Qualify native trust across separate processes, lost ACK, restart and exact retirement.

    Issuers/witness use native fixture stores; both applications own independent
    persistent stores. Only CESR HTTP, signed webhooks, public status and contract
    reads cross process boundaries. No acceptance/result records are injected.
    """
    schemas = [
        scheming.Schemer(raw=p.read_bytes())
        for p in files("eoa_verifier.verification").joinpath("schema_data").iterdir()
    ]
    by_said = {schema.said: schema for schema in schemas}
    rpc, addresses = deployment
    verifier_port, oracle_port, witness_port = free_port(), free_port(), free_port()
    root = tmp_path / "services"
    verifier_aid, verifier_kel = identity(root, "verifier", "verifier")
    oracle_aid, oracle_kel = identity(root, "oracle", "oracle")
    assert verifier_aid != oracle_aid
    kel_path = tmp_path / "verifier.cesr"
    kel_path.write_bytes(verifier_kel)
    effects = effects_for(deployment)
    wallet_key = "0x" + "0" * 63 + "1"
    wallet = Account.from_key(wallet_key).address
    finished = []

    with ExitStack() as stack:
        witness_hby = stack.enter_context(habbing.openHby(name="witness", temp=True))
        witness_doers = indirecting.setupWitness(
            hby=witness_hby,
            alias="witness",
            tcpPort=None,
            httpPort=witness_port,
        )
        witness = witness_hby.habByName("witness")
        witness_url = f"http://127.0.0.1:{witness_port}"
        witness_hby.psr.parse(ims=witness.makeEndRole(eid=witness.pre))
        witness_hby.psr.parse(ims=witness.makeLocScheme(url=witness_url))
        actors, issuers = open_issuers(
            stack, schemas, base="", temp=True, witness=witness
        )
        oobi = f"{witness_url}/oobi/{witness.pre}/controller"
        resolvers = []
        for hby, _, _ in actors.values():
            hby.db.oobis.pin(
                keys=(oobi,), val=basing.OobiRecord(date=helping.nowIso8601())
            )
            resolvers.append(oobiing.Oobiery(hby=hby, clienter=httping.Clienter()))
        _, geda, _ = actors["geda"]
        _, qvi, _ = actors["qvi"]
        _, le, le_rgy = actors["le"]
        proxy = lost_ack_proxy(stack, oracle_port)
        destination = f"http://127.0.0.1:{proxy.server_port}/v1/status-events"
        verifier_command = [
            str(Path(sys.executable).with_name("eoa-verifier")),
            "server",
            "start",
            "--name",
            "verifier",
            "--head-dir",
            str(root),
            "--http-port",
            str(verifier_port),
            "--trusted-geda-aid",
            geda.pre,
            "--rpc-url",
            rpc,
            "--chain-id",
            "31337",
            "--webhook-url",
            destination,
            "--webhook-aid",
            oracle_aid,
            "--webhook-timeout",
            "1",
            "--webhook-interval",
            "0.1",
            "--webhook-backoff-base",
            "0.1",
            "--webhook-backoff-cap",
            "0.2",
        ]
        oracle_command = [
            str(Path(sys.executable).with_name("vlei-oracle")),
            "server",
            "start",
            "--name",
            "oracle",
            "--head-dir",
            str(root),
            "--http-port",
            str(oracle_port),
            "--host",
            "127.0.0.1",
            "--verifier-aid",
            verifier_aid,
            "--verifier-kel",
            str(kel_path),
            "--chain-id",
            "31337",
            "--webhook-url",
            destination,
            "--rpc-url",
            rpc,
            "--endorsement-registry",
            addresses["oracleRegistry"],
            "--identity-registry",
            addresses["identityRegistry"],
            "--confirmations",
            "1",
            "--posting-interval",
            "0.1",
        ]
        environment = {
            **os.environ,
            "VLEI_ORACLE_POSTING_KEY": KEY,
            "PYTHONUNBUFFERED": "1",
        }
        verifier_log, oracle_log = tmp_path / "verifier.log", tmp_path / "oracle.log"
        verifier = launch(stack, verifier_command, verifier_log)
        oracle = launch(stack, oracle_command, oracle_log, env=environment)
        registries, credentials, streams = {}, {}, {}

        def deliver(stream):
            """PUT a finite native CESR stream; transport acceptance is not credential acceptance."""
            code, _ = yield from request(
                workflow, verifier_port, "/", method="PUT", body=stream
            )
            assert code == 204

        def status(said, expected):
            """Wait for the public stored state and authenticated delivery completion."""
            deadline = workflow.tyme + 15
            while True:
                code, body = yield from request(
                    workflow, verifier_port, f"/v1/associations/{said}/status"
                )
                value = json.loads(body)
                if (
                    code == 200
                    and value["status"] == expected
                    and value.get("delivery", {}).get("delivered")
                ):
                    return value
                assert workflow.tyme < deadline, (
                    value,
                    verifier_log.read_text(),
                    oracle_log.read_text(),
                )
                yield 0.05

        def mapping(said):
            """Require actual registry state, not an HTTP ACK or submitted transaction hash."""
            deadline = workflow.tyme + 15
            while True:
                _, current = yield from effects.observe(wallet)
                if current[1] == said:
                    return current
                assert workflow.tyme < deadline, (current, oracle_log.read_text())
                yield 0.05

        def association():
            """Issue a real wallet-signed association at a new witnessed LE KEL event."""
            challenge = build_challenge(
                issuer_aid=le.pre,
                le_vlei_said=credentials["qvi"].said,
                ext_id=f"eip155:31337:{wallet.lower()}",
                nonce=str(uuid4()),
            )
            sig = Account.sign_message(
                encode_typed_data(full_message=challenge), wallet_key
            ).signature
            creder = association_from_challenge(
                challenge, "0x" + sig.hex(), registries["le"].regk
            )
            yield from issue_credential(issuers["le"], registries["le"], creder)
            stream = replay(le, le_rgy, registries["le"], creder) + grant(
                le, le_rgy, creder, verifier_aid
            )
            return creder, stream

        def run(tymth, tock=0.0, **opts):
            """Follow the production success path, then exercise only the important delivery/lifecycle faults."""
            nonlocal verifier, oracle
            yield tock
            yield from ready(verifier, verifier_port, verifier_log, workflow)
            yield from ready(oracle, oracle_port, oracle_log, workflow)
            yield from deliver(oracle_kel)
            for hby, _, _ in actors.values():
                while hby.db.roobi.get(keys=(oobi,)) is None:
                    yield 0.01
                assert hby.db.roobi.get(keys=(oobi,)).state == oobiing.Result.resolved
            yield from approve_qvi(actors, issuers)
            for name, (_, hab, rgy) in actors.items():
                registry = rgy.makeRegistry(name=name, prefix=hab.pre, noBackers=True)
                issuers[name].registrar.incept(registry.vcp, anchor(hab, registry.vcp))
                while not issuers[name].registrar.complete(registry.regk):
                    yield 0.01
                registries[name] = registry
            # Establish the actual GEDA -> QVI -> LE chain before presenting the association.
            for name, recipient, schema in (
                ("geda", qvi, QVI_SCHEMA),
                ("qvi", le, LE_SCHEMA),
            ):
                source = (
                    None
                    if name == "geda"
                    else coring.Saider.saidify(
                        sad={
                            "d": "",
                            "qvi": {"n": credentials["geda"].said, "s": QVI_SCHEMA},
                        }
                    )[1]
                )
                creder = issuers[name].create(
                    regname=name,
                    recp=recipient.pre,
                    schema=schema,
                    source=source,
                    rules=rules(by_said[schema]),
                    data={"LEI": "5493001KJTIIGC8Y1R12"},
                )
                yield from issue_credential(issuers[name], registries[name], creder)
                credentials[name] = creder
                streams[name] = replay(
                    registries[name].hab, issuers[name].rgy, registries[name], creder
                )
                hby, _, rgy = actors["qvi" if name == "geda" else "le"]
                receive(
                    hby,
                    rgy,
                    streams["geda"] + (streams["qvi"] if name == "qvi" else b""),
                )
                assert rgy.reger.saved.get(keys=(creder.said,))
            first, first_stream = yield from association()
            yield from deliver(streams["geda"] + streams["qvi"] + first_stream)
            accepted = yield from status(first.said, "accepted")
            assert accepted["facts"]["issuerAid"] == le.pre
            assert accepted["observation"]["chainId"] == 31337
            yield from mapping(first.said)
            assert proxy.dropped and accepted["delivery"]["attempts"] >= 2
            initial = [
                body
                for body in proxy.events
                if json.loads(body)["eventId"] == f"{first.said}:1"
            ]
            assert len(initial) >= 2 and len(set(initial)) == 1

            # Reopen both independent applications; no new Grant or account observation is needed.
            stop(verifier)
            stop(oracle)
            oracle = launch(stack, oracle_command, oracle_log, env=environment)
            yield from ready(oracle, oracle_port, oracle_log, workflow)
            yield from mapping(first.said)  # The verifier remains offline.
            verifier = launch(stack, verifier_command, verifier_log)
            yield from ready(verifier, verifier_port, verifier_log, workflow)
            reopened = yield from status(first.said, "accepted")
            assert reopened["observation"] == accepted["observation"]
            yield from deliver(
                first_stream
            )  # Duplicate presentation has no new application effects.
            assert (yield from status(first.said, "accepted"))["eventId"] == accepted[
                "eventId"
            ]

            second, second_stream = yield from association()
            yield from deliver(second_stream)
            yield from status(second.said, "accepted")
            yield from mapping(second.said)
            # A Grant still carries issuance. Retirement requires the native revocation TEL and its anchor.
            yield from revoke_credential(issuers["le"], registries["le"], first)
            yield from deliver(
                grant(le, le_rgy, first, verifier_aid)
                + replay(le, le_rgy, registries["le"], first)
            )
            yield from status(first.said, "revoked")
            yield from mapping(second.said)
            yield from revoke_credential(issuers["le"], registries["le"], second)
            yield from deliver(
                grant(le, le_rgy, second, verifier_aid)
                + replay(le, le_rgy, registries["le"], second)
            )
            retired = yield from status(second.said, "revoked")
            assert retired["delivery"]["eventId"] == f"{second.said}:2"
            yield from mapping("")
            finished.append(True)
            workflow.remove(list(workflow.doers[:-1]))

        doers = [
            *witness_doers,
            *(d for r in resolvers for d in r.doers),
            *(d for i in issuers.values() for d in (i.registrar, i)),
            IssuerEscrows(list(issuers.values())),
            effects,
        ]
        workflow = doing.DoDoer(doers=[*doers, doing.doify(run)])
        doing.Doist(real=True, tock=0.01, limit=90).do(doers=[workflow])
        assert finished, (verifier_log.read_text(), oracle_log.read_text())

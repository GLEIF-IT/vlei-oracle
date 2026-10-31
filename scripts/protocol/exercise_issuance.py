"""Issue a delegated vLEI chain and exercise the oracle through IPEX/HTTP.

Uses the KLI's Registrar and Credentialer under one HIO scheduler. GEDA, QVI,
LE, and oracle have separate KERI stores (temporary unless --keep is set).
Identifiers are single-sig with one local witness; GEDA is a test trust root.
See README.md for setup and the reasons for direct transport in this exercise.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import socket
import subprocess
import sys
from time import monotonic
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ceremony import association_from_challenge, write_credential
from vlei_oracle.noncing import NonceLedger
from transport_fixture import PresentationDoer
from relationship_fixture import qualify_relationships
from replacement_fixture import qualify_replacement
from process_fixture import (
    authorize_writer, qualify_after_submission, qualify_before_submission,
    qualify_verification,
)
from contract_fixture import DEPLOYER_KEY, POSTER_KEY, deploy, qualify_transfers, read, send
from eth_account import Account
from eth_account.messages import encode_typed_data
from hio.base import doing
from hio.core import http
from issuance_helpers import (
    IssuerEscrows,
    anchor,
    approve_qvi,
    grant,
    issuer_for,
    issue_credential,
    revoke_credential,
    kli_issue,
    open_issuers,
    receive,
    replay,
    rules,
)
from keri.app import habbing, httping, indirecting, oobiing
from keri.core import coring, scheming
from keri.db import basing, dbing
from keri.help import helping
from keri.vdr import credentialing

from vlei_oracle.verification.credentials import assess_association, reconstruct_association_mapping, verify_association
from vlei_oracle.verification.constants import LE_SCHEMA, QVI_SCHEMA
from vlei_oracle.endorsement.effects import ContractEffects
from vlei_oracle.verification.eoa import (
    build_challenge,
    normalize_ext_id,
)
from vlei_oracle.verification.errors import MissingCredentialArtifactsError, VerificationError
from vlei_oracle.endorsement.reconciling import EndorsementDoer
from vlei_oracle.serving import ServiceConfig, setup_service

ROOT = Path(__file__).resolve().parents[2]
ASSOCIATION_SCHEMA = ROOT / "protocol/external-association-v1/association.schema.json"
PUBLIC_TEST_KEY = "0x" + "0" * 63 + "1"


def free_port() -> int:
    """Choose an available loopback port for a service started by this exercise."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]



def exercise(schema_dir: Path, *, keep: bool = False, comply: Path | None = None,
             process_check: bool = False, kli_check: bool = False,
             two_chain_check: bool = False) -> None:
    """Set up isolated actors, then schedule the complete issuance/HTTP workflow.

    ``schema_dir`` supplies QVI and LE schemas; this checkout supplies the
    association schema. ExitStack owns actor stores and the Anvil subprocess.
    One Doist schedules issuance, transport, oracle verification, and assertions.
    ``keep`` retains stores under a fresh KLI-compatible base, even on failure;
    it does not keep the HTTP service or Anvil running.
    """
    keep = keep or kli_check
    temp = not keep
    base = f"issuance-{uuid4().hex}" if keep else ""
    if keep:
        print(f"Keeping KERI stores under base: {base}", flush=True)

    # 1. Share schema definitions, while keeping every actor's KERI state separate.
    schemas = [scheming.Schemer(raw=p.read_bytes()) for p in schema_dir.glob("*.json")]
    schemas.append(scheming.Schemer(raw=ASSOCIATION_SCHEMA.read_bytes()))
    by_said = {schema.said: schema for schema in schemas}
    schema_said = schemas[-1].said
    with ExitStack() as stack:
        artifacts = Path(
            stack.enter_context(TemporaryDirectory(prefix="vlei-ceremony-"))
        )
        # 2. Create single-key GEDA, delegated QVI, and LE identifiers and issuers.
        witness_port = free_port()
        witness_hby = stack.enter_context(
            habbing.openHby(name="association-witness", base=base, temp=temp)
        )
        witness_alias = "witness-" + uuid4().hex
        witness_doers = indirecting.setupWitness(
            hby=witness_hby, alias=witness_alias, tcpPort=None, httpPort=witness_port
        )
        witness = witness_hby.habByName(witness_alias)
        witness_url = f"http://127.0.0.1:{witness_port}"
        witness_hby.psr.parse(ims=witness.makeEndRole(eid=witness.pre))
        witness_hby.psr.parse(ims=witness.makeLocScheme(url=witness_url))
        actors, issuers = open_issuers(
            stack, schemas, base=base, temp=temp, witness=witness
        )
        oobi = f"{witness_url}/oobi/{witness.pre}/controller"
        oobiers = []
        for hby, _, _ in actors.values():
            hby.db.oobis.pin(
                keys=(oobi,), val=basing.OobiRecord(date=helping.nowIso8601())
            )
            oobiers.append(oobiing.Oobiery(hby=hby, clienter=httping.Clienter()))
        _geda_hby, geda, _geda_rgy = actors["geda"]
        _qvi_hby, qvi, qvi_rgy = actors["qvi"]
        le_hby, le, le_rgy = actors["le"]
        nonce_ledger = stack.enter_context(
            NonceLedger(Path(le_rgy.reger.path) / "association-nonces")
        )

        # 4. Start an isolated chain and oracle; their clients share the HIO clock.
        rpc_port, oracle_port = free_port(), free_port()
        anvil = stack.enter_context(
            subprocess.Popen(
                [
                    shutil.which("anvil") or "anvil",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(rpc_port),
                    "--silent",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        stack.callback(anvil.terminate)
        rpc_url = f"http://127.0.0.1:{rpc_port}"
        runtime = setup_service(
            ServiceConfig(
                name="association-oracle",
                alias="oracle",
                base=base,
                temp=temp,
                no_post=True,
                host="127.0.0.1",
                http_port=oracle_port,
                trusted_geda_aid=geda.pre,
                association_schema=str(ASSOCIATION_SCHEMA),
                schema_dir=str(schema_dir),
                rpc_url=rpc_url,
            )
        )
        stack.callback(runtime.baser.close, clear=temp)
        stack.callback(runtime.rgy.reger.close, clear=temp)
        if keep:
            # Print before scheduling so failed exercises are also inspectable.
            kli = str(Path(sys.executable).with_name("kli"))
            for hby, hab, rgy in [
                *actors.values(),
                (runtime.hby, runtime.hab, runtime.rgy),
            ]:
                print(f"\n{hab.name}: {hab.pre}")
                print(f"  Keys: {hby.ks.path}\n  Events: {hby.db.path}")
                print(f"  Credentials: {rgy.reger.path}")
                args = ["--name", hby.name, "--base", base, "--alias", hab.name]
                print(shlex.join([kli, "status", *args]))
                print(shlex.join([kli, "vc", "list", *args, "--verbose"]))
            print(f"\nOracle operations: {runtime.baser.path}", flush=True)
        rpc = http.Client(hostname="127.0.0.1", port=rpc_port, reconnectable=True)
        issuer_escrows = IssuerEscrows(list(issuers.values()))
        doers = [
            *witness_doers,
            *(doer for resolver in oobiers for doer in resolver.doers),
            *runtime.doers,
            http.ClientDoer(client=rpc),
            issuer_escrows,
        ]
        for issuer in issuers.values():
            doers.extend([issuer.registrar, issuer])
        outcome = {}
        propagation = {}
        final_operations = []

        def run(tymth, tock=0.0, **opts):
            """Advance explicit workflow steps while HIO children do the work."""
            nonlocal le_hby, le, le_rgy
            yield tock
            # 5. Wait for Anvil through a nonblocking HIO JSON-RPC request.
            rpc.request(
                method="POST",
                path="/",
                data={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
            )
            while not rpc.responses:
                yield 0.01
            response = json.loads(rpc.responses.popleft()["body"])
            assert int(response["result"], 16) == 31337, response

            for hby, _, _ in actors.values():
                while hby.db.roobi.get(keys=(oobi,)) is None:
                    yield 0.01
                assert hby.db.roobi.get(keys=(oobi,)).state == oobiing.Result.resolved
            yield from approve_qvi(actors, issuers)
            print(
                "Witness OOBI discovery and receipted delegation completed", flush=True
            )

            # 6. Use Registrar.incept and its completion signal, as `kli vc registry incept` does.
            registries = {}
            for name, (_, hab, rgy) in actors.items():
                registry = rgy.makeRegistry(name=name, prefix=hab.pre, noBackers=True)
                issuer = issuers[name]
                issuer.registrar.incept(registry.vcp, anchor(hab, registry.vcp))
                while not issuer.registrar.complete(registry.regk):
                    yield 0.01
                registries[name] = registry

            # 7. GEDA issues QVI, then QVI issues LE, using the KLI create/issue APIs.
            credentials, streams = {}, {}
            for name, recipient, schema_said_source in (
                ("geda", qvi, QVI_SCHEMA),
                ("qvi", le, LE_SCHEMA),
            ):
                issuer = issuers[name]
                source = None
                if name == "qvi":
                    source = coring.Saider.saidify(
                        sad={
                            "d": "",
                            "qvi": {"n": credentials["geda"].said, "s": QVI_SCHEMA},
                        }
                    )[1]
                creder = issuer.create(
                    regname=name,
                    recp=recipient.pre,
                    schema=schema_said_source,
                    source=source,
                    rules=rules(by_said[schema_said_source]),
                    data={
                        "LEI": "5493001KJTIIGC8Y1R12"
                        if name == "geda"
                        else "254900OPPU84GM83MG36"
                    },
                )
                yield from issue_credential(issuer, registries[name], creder)
                credentials[name] = creder
                streams[name] = replay(
                    registries[name].hab, issuer.rgy, registries[name], creder
                )
                upstream = streams["geda"] + (streams["qvi"] if name == "qvi" else b"")
                holder_hby, _, holder_rgy = actors["qvi" if name == "geda" else "le"]
                receive(holder_hby, holder_rgy, upstream)
                assert holder_rgy.reger.saved.get(keys=(creder.said,))
            assert le_hby.kevers[qvi.pre].delpre == geda.pre
            le_credential = credentials["qvi"]
            print(
                "GEDA delegation and QVI/LE credential issuance completed", flush=True
            )

            # 8. Assemble schema-specific data; KERIpy constructs and SAIDifies the ACDC.
            # Full-credential issuance matches `kli vc create --credential`. Its data
            # shortcut adds a subject nonce for private credentials, which this schema omits.
            account = Account.from_key(PUBLIC_TEST_KEY)
            associations = {}
            cases = [
                ("valid", PUBLIC_TEST_KEY, 31337),
                ("wrong_signer", "0x" + "0" * 63 + "2", 31337),
                ("wrong_chain", PUBLIC_TEST_KEY, 31338),
                ("contract", PUBLIC_TEST_KEY, 31337),
                ("delegated", PUBLIC_TEST_KEY, 31337),
                ("withheld", PUBLIC_TEST_KEY, 31337),
            ]
            if comply is not None:
                cases.append(("beneficiary", "0x" + "0" * 63 + "2", 31337))
            if process_check:
                cases.append(("process", "0x" + "0" * 63 + "5", 31337))
                if comply is not None:
                    cases.append(("process_broadcast", "0x" + "0" * 63 + "6", 31337))
            for label, key, chain_id in cases:
                nonce = str(uuid4())
                wallet = Account.from_key(key).address if label in ("beneficiary", "process", "process_broadcast") else account.address
                ext_id = normalize_ext_id(chain_id, wallet)
                challenge = build_challenge(
                    issuer_aid=le.pre,
                    le_vlei_said=le_credential.said,
                    ext_id=ext_id,
                    nonce=nonce,
                )
                signature = Account.sign_message(
                    encode_typed_data(full_message=challenge), key
                ).signature
                if label == "wrong_signer":
                    # Deliberately issue an invalid test artifact; the supported
                    # ceremony rejects it before nonce consumption or issuance.
                    creder = association_from_challenge(
                        challenge, "0x" + signature.hex(), registries["le"].regk
                    )
                else:
                    creder = write_credential(
                        challenge=challenge,
                        signature="0x" + signature.hex(),
                        registry=registries["le"].regk,
                        nonce_ledger=nonce_ledger,
                        output=artifacts / f"{label}.json",
                    )
                issuer = issuers["le"]
                issuer.validate(creder)
                yield from issue_credential(issuer, registries["le"], creder)
                associations[label] = creder

            if kli_check:
                key = "0x" + "0" * 63 + "8"
                challenge = build_challenge(
                    issuer_aid=le.pre, le_vlei_said=le_credential.said,
                    ext_id=normalize_ext_id(31337, Account.from_key(key).address),
                    nonce=str(uuid4()),
                )
                signature = Account.sign_message(encode_typed_data(full_message=challenge), key).signature
                cli_file = artifacts / "kli.json"
                creder = write_credential(
                    challenge=challenge, signature="0x" + signature.hex(),
                    registry=registries["le"].regk,
                    nonce_ledger=nonce_ledger,
                    output=cli_file,
                )
                # Give one writer exclusive control; the witness and other
                # actors keep running while KLI owns the LE stores.
                old_issuer = issuers["le"]
                workflow.remove([old_issuer, old_issuer.registrar, *oobiers[-1].doers])
                issuer_escrows.issuers.remove(old_issuer)
                le_rgy.reger.close()
                le_hby.close()
                yield from kli_issue(base=base, credential=cli_file, artifacts=artifacts, workflow=workflow)
                le_hby = stack.enter_context(habbing.openHby(name="le", base=base, temp=False))
                le = le_hby.habByName("le")
                le_rgy = credentialing.Regery(hby=le_hby, name="le", base=base)
                stack.callback(le_rgy.reger.close)
                actors["le"] = (le_hby, le, le_rgy)
                issuers["le"] = issuer_for(le_hby, le_rgy)
                issuer_escrows.issuers.append(issuers["le"])
                registries["le"] = le_rgy.registryByName("le")
                reopened_doers = [issuers["le"].registrar, issuers["le"]]
                workflow.extend(reopened_doers)
                doers.extend(reopened_doers)
                assert le_rgy.reger.saved.get(keys=(creder.said,))
                assert le_rgy.reger.creds.get(keys=(creder.said,)).raw == creder.raw
                cli_port = free_port()
                cli_runtime = setup_service(ServiceConfig(
                    name="kli-oracle", base=base, temp=False, no_post=True,
                    host="127.0.0.1", http_port=cli_port, trusted_geda_aid=geda.pre,
                    schema_dir=str(schema_dir), rpc_url=rpc_url,
                ))
                workflow.extend(cli_runtime.doers)
                check = PresentationDoer(
                    port=cli_port, recipient=cli_runtime.hab.pre,
                    stream=streams["geda"] + streams["qvi"]
                    + replay(le, le_rgy, registries["le"], creder)
                    + grant(le, le_rgy, creder, cli_runtime.hab.pre),
                    said=creder.said, expected="verified",
                )
                workflow.extend([check])
                while not check.done:
                    yield 0.01
                workflow.remove([check])
                workflow.remove(cli_runtime.doers)
                print("KLI issued the pre-signed untargeted ACDC and HTTP verification passed", flush=True)

            # Missing TEL/anchor evidence in fresh receivers must stay pending.
            # These are HTTP acceptance checks, independent of the original session.
            for missing in ("issuance", "anchor"):
                fresh_port = free_port()
                fresh = setup_service(
                    ServiceConfig(
                        name=f"missing-{missing}",
                        alias="oracle",
                        base=base,
                        temp=temp,
                        no_post=True,
                        host="127.0.0.1",
                        http_port=fresh_port,
                        trusted_geda_aid=geda.pre,
                        association_schema=str(ASSOCIATION_SCHEMA),
                        schema_dir=str(schema_dir),
                        rpc_url=rpc_url,
                    )
                )
                stack.callback(fresh.baser.close, clear=temp)
                stack.callback(fresh.rgy.reger.close, clear=temp)
                association = associations["valid"]
                le_events = list(le.db.clonePreIter(pre=le.pre))
                evidence = streams["geda"] + streams["qvi"]
                evidence += b"".join(
                    le_events if missing == "issuance" else le_events[:1]
                )
                evidence += b"".join(
                    le_rgy.reger.clonePreIter(pre=registries["le"].regk)
                )
                evidence += grant(
                    le,
                    le_rgy,
                    association,
                    fresh.hab.pre,
                    include_issuance=missing != "issuance",
                    include_anchor=missing != "anchor",
                )
                check = PresentationDoer(
                    port=fresh_port,
                    recipient=fresh.hab.pre,
                    stream=evidence,
                    said=association.said,
                    expected="awaiting_artifacts",
                )
                workflow.extend([*fresh.doers, check])
                while not check.done:
                    yield 0.01
                assert not check.result["managed"] and not check.result["transactions"]
                assert fresh.rgy.reger.saved.get(keys=(association.said,)) is None
                workflow.remove([*fresh.doers, check])
            print(
                "Fresh receivers keep missing TEL/anchor evidence pending", flush=True
            )

            # 9. A complete Grant arrives before its sources; HTTP operation stays pending.
            association = associations["valid"]
            check = PresentationDoer(
                port=oracle_port,
                recipient=runtime.hab.pre,
                stream=b"".join(le.db.clonePreIter(pre=le.pre))
                + grant(le, le_rgy, association, runtime.hab.pre),
                said=association.said,
                expected="awaiting_artifacts",
            )
            workflow.extend([check])
            while not check.done:
                yield 0.01
            workflow.remove([check])
            print("Grant pending until source evidence arrives", flush=True)

            # 10. Deliver the chain and assert the independently verified association mapping.
            check = PresentationDoer(
                port=oracle_port,
                recipient=runtime.hab.pre,
                stream=streams["geda"]
                + streams["qvi"]
                + replay(le, le_rgy, registries["le"], association),
                said=association.said,
                expected="verified",
            )
            workflow.extend([check])
            while not check.done:
                yield 0.01
            workflow.remove([check])
            verified = check.result
            assert reconstruct_association_mapping(rgy=runtime.rgy, said=association.said, schema_said=schema_said)["wallet"] == account.address.lower(), verified
            assert verified["associationSaid"] == association.said and verified["managed"], verified
            assert not verified["transactions"], verified
            try:
                verify_association(
                    rgy=runtime.rgy,
                    said=association.said,
                    schema_said=schema_said,
                    trusted_geda_aid=le.pre,
                )
            except VerificationError as exc:
                assert "trusted GEDA" in str(exc), exc
            else:
                raise AssertionError("Unrelated trust root passed verification")

            # Direct real-store negative: acceptance alone must not bypass receipts.
            state = runtime.rgy.reger.tevers[association.regi].vcState(association.said)
            anchor_key = dbing.dgKey(association.issuer, state.a["d"])
            receipt_sigs = [bytes(wig) for wig in runtime.hby.db.getWigs(anchor_key)]
            assert receipt_sigs
            runtime.hby.db.delWigs(anchor_key)
            try:
                try:
                    verify_association(
                        rgy=runtime.rgy,
                        said=association.said,
                        schema_said=schema_said,
                        trusted_geda_aid=geda.pre,
                    )
                except MissingCredentialArtifactsError as exc:
                    assert "witness receipts" in str(exc), exc
                else:
                    raise AssertionError(
                        "Accepted index bypassed missing witness receipts"
                    )
            finally:
                runtime.hby.db.putWigs(anchor_key, receipt_sigs)
            print("Accepted association still requires witness receipts", flush=True)

            # 11. Check bad signatures/networks, ordinary contracts, and EIP-7702 EOAs.
            for label, expected, reason in [
                ("wrong_signer", "failed", "signer does not match"),
                ("wrong_chain", "failed", "does not match oracle network"),
                ("contract", "failed", "contract code"),
                ("delegated", "verified", ""),
            ]:
                if label in ("contract", "delegated"):
                    code = "0x6000" if label == "contract" else "0xef0100" + "12" * 20
                    rpc.request(
                        method="POST",
                        path="/",
                        data={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "anvil_setCode",
                            "params": [account.address, code],
                        },
                    )
                    while not rpc.responses:
                        yield 0.01
                    response = json.loads(rpc.responses.popleft()["body"])
                    assert "error" not in response, response
                creder = associations[label]
                check = PresentationDoer(
                    port=oracle_port,
                    recipient=runtime.hab.pre,
                    stream=replay(le, le_rgy, registries["le"], creder)
                    + grant(le, le_rgy, creder, runtime.hab.pre),
                    said=creder.said,
                    expected=expected,
                )
                workflow.extend([check])
                while not check.done:
                    yield 0.01
                workflow.remove([check])
                assert reason in check.result["failureReason"], check.result
                assert not check.result["transactions"], check.result
                if expected == "failed":
                    assert not check.result["managed"], check.result
            print("Signer, chain, contract, and EIP-7702 cases passed", flush=True)
            yield from qualify_relationships(
                actors=actors, issuers=issuers, registries=registries, credentials=credentials,
                schemas=by_said, streams=streams, runtime=runtime,
                oracle_port=oracle_port, workflow=workflow, stack=stack,
                base=base, temp=temp, witness=witness, witness_url=witness_url,
            )
            if process_check:
                process_creder = associations["process"]
                process_config = ServiceConfig(
                    name="process-oracle", base="process-" + uuid4().hex,
                    alias="oracle", http_port=free_port(), no_post=True,
                    trusted_geda_aid=geda.pre, schema_dir=str(schema_dir.resolve()),
                    rpc_url=f"http://127.0.0.1:{free_port()}",
                )
                process_config, process_aid, process_result = yield from qualify_verification(
                    config=process_config, stack=stack, artifacts=artifacts, workflow=workflow,
                    partial=lambda aid: b"".join(le.db.clonePreIter(pre=le.pre))
                    + grant(le, le_rgy, process_creder, aid),
                    complete=streams["geda"] + streams["qvi"]
                    + replay(le, le_rgy, registries["le"], process_creder),
                    association_said=process_creder.said, working_rpc=rpc_url,
                )

            if comply is not None:
                # Present a second wallet of the same entity before enabling posting.
                beneficiary = Account.from_key("0x" + "0" * 63 + "2")
                creder = associations["beneficiary"]
                check = PresentationDoer(
                    port=oracle_port, recipient=runtime.hab.pre,
                    stream=replay(le, le_rgy, registries["le"], creder)
                    + grant(le, le_rgy, creder, runtime.hab.pre),
                    said=creder.said, expected="verified",
                )
                workflow.extend([check])
                while not check.done:
                    yield 0.01
                workflow.remove([check])

                target_saids = [associations[label].said for label in ("delegated", "beneficiary")]
                fixture_effects, token, wrapper, children = yield from qualify_transfers(
                    comply=comply, rpc_url=rpc_url, artifacts=artifacts,
                    runtime=runtime, trusted_geda=geda.pre, schema_said=schema_said,
                    sender_key=PUBLIC_TEST_KEY, recipient=beneficiary.address,
                    target_saids=target_saids, workflow=workflow,
                )
                doers.extend(children)
                associations["beneficiary"], propagation["supersessionSeconds"] = yield from qualify_replacement(
                    le=le, rgy=le_rgy, registry=registries["le"], issuer=issuers["le"],
                    le_said=le_credential.said, previous=associations["beneficiary"],
                    key="0x" + "0" * 63 + "2", runtime=runtime, port=oracle_port,
                    effects=fixture_effects, workflow=workflow, artifacts=artifacts,
                    ledger=nonce_ledger,
                )
                target_saids[1] = associations["beneficiary"].said

                old = associations["valid"]
                while runtime.baser.get_managed(old.said).state != "withdrawn":
                    yield 0.01
                check = PresentationDoer(
                    port=oracle_port, recipient=runtime.hab.pre,
                    stream=grant(le, le_rgy, old, runtime.hab.pre),
                    said=old.said, expected="withdrawn",
                )
                workflow.extend([check])
                while not check.done:
                    yield 0.01
                workflow.remove([check])
                _, endorsement = yield from fixture_effects.observe(account.address)
                assert endorsement[1] == associations["delegated"].said
                assert not any(intent.associationSaid == old.said and
                               intent.action == "publishEndorsement"
                               for _, intent in runtime.baser.transactions.getItemIter())
                # Real-store boundary: a missing local operation does not make
                # the already published later association older than this one.
                posting = children[-1]
                current_said = endorsement[1]
                current_op = runtime.baser.get_managed(current_said)
                current_creder = runtime.rgy.reger.creds.get(keys=(current_said,))
                runtime.baser.managed.rem(keys=(current_said,))
                try:
                    old_op = runtime.baser.get_managed(old.said)
                    assert posting.desired(old_op)
                    assert posting.chain_order(old_op, endorsement)[0] is False
                    runtime.rgy.reger.creds.rem(keys=(current_said,))
                    try:
                        try:
                            posting.chain_order(old_op, endorsement)
                        except MissingCredentialArtifactsError as exc:
                            assert "current on-chain association" in str(exc), exc
                        else:
                            raise AssertionError("Missing current evidence allowed replacement")
                    finally:
                        runtime.rgy.reger.creds.pin(keys=(current_said,), val=current_creder)
                finally:
                    runtime.baser.pin_managed(current_op)
                # Controlled worker-result boundary over the real KERIpy stores:
                # newer work arriving during RPC must invalidate the old dispatch.
                checker = EndorsementDoer(
                    baser=runtime.baser, rgy=runtime.rgy, trusted_geda_aid=geda.pre,
                    association_schema=schema_said, private_key=POSTER_KEY,
                    effects=ContractEffects(
                        rpc_url=rpc_url, chain_id=31337,
                        writer=Account.from_key(POSTER_KEY).address,
                        endorsement=fixture_effects.contracts["endorsement"].address,
                        identity=fixture_effects.contracts["identity"].address,
                    ),
                )
                runtime.baser.managed.rem(keys=(current_said,))
                pending = checker.evidence(old_op)
                try:
                    next(pending)
                    assert checker.eoa.msgs
                    runtime.baser.pin_managed(current_op)
                    checker.eoa.cues.append({
                        "said": old.said, "kin": "verified", "eoaResult": {"accountObservation": {"chainId": 31337}},
                    })
                    try:
                        next(pending)
                    except StopIteration as result:
                        assert result.value[0] is False and "superseded" in result.value[1]
                    else:
                        raise AssertionError("Superseded dispatch survived its RPC boundary")
                finally:
                    pending.close()
                    runtime.baser.pin_managed(current_op)
                print("On-chain issuance evidence blocks stale updates without local history", flush=True)
                print("Unendorsed same-entity wallet and reordered old Grant cannot bypass policy", flush=True)
                if process_check:
                    process_key = "0x" + "0" * 63 + "4"
                    yield from authorize_writer(effects=fixture_effects, comply=comply, key=process_key)
                    process_config = replace(
                        process_config, no_post=False, chain_id=31337,
                        endorsement_registry=fixture_effects.contracts["endorsement"].address,
                        identity_registry=fixture_effects.contracts["identity"].address,
                        posting_key=process_key, confirmations=1, posting_interval=0.05,
                    )
                    posting_process, process_aid, posted_result = yield from qualify_before_submission(
                        config=process_config, effects=fixture_effects,
                        association_said=process_creder.said, proxy_port=free_port(),
                        stack=stack, artifacts=artifacts, workflow=workflow,
                    )
                    broadcast_key = "0x" + "0" * 63 + "7"
                    yield from authorize_writer(effects=fixture_effects, comply=comply, key=broadcast_key)
                    broadcast_config = replace(
                        process_config, name="broadcast-oracle",
                        base="process-" + uuid4().hex, http_port=free_port(),
                        posting_key=broadcast_key,
                    )
                    broadcast_creder = associations["process_broadcast"]
                    print(f"Retaining process-test stores: --name {broadcast_config.name} --base {broadcast_config.base}", flush=True)
                    broadcast_process, broadcast_aid, broadcast_result = yield from qualify_after_submission(
                        config=broadcast_config, effects=fixture_effects,
                        association_said=broadcast_creder.said,
                        evidence=lambda aid: streams["geda"] + streams["qvi"]
                        + replay(le, le_rgy, registries["le"], broadcast_creder)
                        + grant(le, le_rgy, broadcast_creder, aid),
                        stack=stack, artifacts=artifacts, workflow=workflow,
                    )

            if two_chain_check:
                second_rpc_port, second_oracle_port = free_port(), free_port()
                second_anvil = stack.enter_context(subprocess.Popen(
                    [shutil.which("anvil") or "anvil", "--host", "127.0.0.1",
                     "--port", str(second_rpc_port), "--chain-id", "31338", "--silent"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ))
                stack.callback(second_anvil.terminate)
                second_rpc_url = f"http://127.0.0.1:{second_rpc_port}"
                probe = ContractEffects(
                    rpc_url=second_rpc_url, chain_id=31338,
                    writer=Account.from_key(DEPLOYER_KEY).address,
                    endorsement=account.address, identity=account.address,
                )
                workflow.extend([probe])
                yield from probe.check_chain()
                workflow.remove([probe])
                second_addresses = yield from deploy(comply, second_rpc_url, artifacts)
                second_effects = ContractEffects(
                    rpc_url=second_rpc_url, chain_id=31338,
                    writer=Account.from_key(DEPLOYER_KEY).address,
                    endorsement=second_addresses["oracleRegistry"], identity=second_addresses["identityRegistry"],
                    confirmations=1,
                )
                workflow.extend([second_effects])
                doers.append(second_effects)
                yield from authorize_writer(effects=second_effects, comply=comply, key=POSTER_KEY)
                second_runtime = setup_service(ServiceConfig(
                    name="second-chain-oracle", base=base, temp=temp, no_post=False,
                    host="127.0.0.1", http_port=second_oracle_port,
                    trusted_geda_aid=geda.pre, schema_dir=str(schema_dir), rpc_url=second_rpc_url,
                    chain_id=31338, endorsement_registry=second_addresses["oracleRegistry"],
                    identity_registry=second_addresses["identityRegistry"], posting_key=POSTER_KEY,
                    confirmations=1, posting_interval=0.05,
                ))
                workflow.extend(second_runtime.doers)
                doers.extend(second_runtime.doers)
                for label, expected in (("valid", "failed"), ("wrong_chain", "endorsed")):
                    creder = associations[label]
                    check = PresentationDoer(
                        port=second_oracle_port, recipient=second_runtime.hab.pre,
                        stream=streams["geda"] + streams["qvi"]
                        + replay(le, le_rgy, registries["le"], creder)
                        + grant(le, le_rgy, creder, second_runtime.hab.pre),
                        said=creder.said, expected=expected,
                    )
                    workflow.extend([check])
                    while not check.done:
                        yield 0.01
                    workflow.remove([check])
                    if expected == "failed":
                        assert "oracle network" in check.result["failureReason"]
                        assert not check.result["managed"] and not check.result["transactions"]
                        assert (yield from second_effects.observe(account.address))[1] == ("0x" + "00" * 32, "")
                assert (yield from second_effects.observe(account.address))[1][1] == associations["wrong_chain"].said
                assert (yield from fixture_effects.observe(account.address))[1][1] == associations["delegated"].said
                print("Actual two-chain posting isolates signed chain scope and registry state", flush=True)

            # Revoke the beneficiary association itself while its LE source is live.
            if comply is not None:
                revoked_association = associations["beneficiary"]
                yield from revoke_credential(issuers["le"], registries["le"], revoked_association)
                started = monotonic()
                check = PresentationDoer(
                    port=oracle_port, recipient=runtime.hab.pre,
                    stream=replay(le, le_rgy, registries["le"], revoked_association),
                    said=revoked_association.said, expected="withdrawn",
                )
                workflow.extend([check])
                while not check.done:
                    assert monotonic() - started < 30, "association withdrawal exceeded local 30s bound"
                    yield 0.01
                workflow.remove([check])
                assert (yield from fixture_effects.observe(beneficiary.address))[1] == ("0x" + "00" * 32, "")
                assert (yield from fixture_effects.observe(account.address))[1][1] == associations["delegated"].said
                try:
                    verify_association(rgy=runtime.rgy, said=revoked_association.said,
                                       schema_said=schema_said, trusted_geda_aid=geda.pre)
                except VerificationError as exc:
                    assert "revoked" in str(exc), exc
                else:
                    raise AssertionError("Revoked association passed fresh verification")
                assert (yield from read(fixture_effects, wrapper, "transfer", [beneficiary.address, 1000000], sender=account.address)) == (False,)
                yield from send(fixture_effects, wrapper, "transfer", [beneficiary.address, 1000000], PUBLIC_TEST_KEY)
                assert (yield from read(fixture_effects, token, "balanceOf", [beneficiary.address])) == (1000000,)
                propagation["associationRevocationSeconds"] = round(monotonic() - started, 3)
                assert propagation["associationRevocationSeconds"] < 30
                print("Association revocation withdraws only its endorsement and rejects transfer", flush=True)

            # 12. Revoke LE through Registrar, then present a previously withheld child.
            yield from revoke_credential(issuers["qvi"], registries["qvi"], le_credential)
            revocation_started = monotonic()
            source_stream = replay(qvi, qvi_rgy, registries["qvi"], le_credential)
            revocation_targets = []
            if comply is not None and process_check:
                revocation_targets.extend([
                    (process_config.http_port, process_aid, process_creder.said),
                    (broadcast_config.http_port, broadcast_aid, broadcast_creder.said),
                ])
            if two_chain_check:
                revocation_targets.append((second_oracle_port, second_runtime.hab.pre,
                                           associations["wrong_chain"].said))
            # Every receiver owns its HTTP doer; deliver observed revocation
            # concurrently rather than making later receivers wait for earlier ones.
            revocation_checks = [PresentationDoer(port=port, recipient=aid,
                                                 stream=source_stream, said=said, expected="withdrawn")
                                 for port, aid, said in revocation_targets]
            workflow.extend(revocation_checks)
            withheld = associations["withheld"]
            check = PresentationDoer(
                port=oracle_port,
                recipient=runtime.hab.pre,
                stream=replay(qvi, qvi_rgy, registries["qvi"], le_credential)
                + replay(le, le_rgy, registries["le"], withheld)
                + grant(le, le_rgy, withheld, runtime.hab.pre),
                said=withheld.said,
                expected="failed",
            )
            workflow.extend([check])
            while not check.done:
                yield 0.01
            workflow.remove([check])
            while any(not delivery.done for delivery in revocation_checks):
                assert monotonic() - revocation_started < 30, "source fanout exceeded local 30s bound"
                yield 0.01
            workflow.remove(revocation_checks)
            assert not check.result["managed"] and not check.result["transactions"], (
                check.result
            )
            assert "revoked" in check.result["failureReason"], check.result
            try:
                verify_association(
                    rgy=runtime.rgy,
                    said=association.said,
                    schema_said=schema_said,
                    trusted_geda_aid=geda.pre,
                )
            except VerificationError as exc:
                assert "revoked" in str(exc), exc
            else:
                raise AssertionError("Revoked LE passed fresh verification")
            association_mapping = reconstruct_association_mapping(rgy=runtime.rgy, said=association.said,
                                          schema_said=schema_said)
            assert association_mapping["wallet"] == association.attrib["extId"].split(":")[2]
            assert association_mapping["associationSaid"] == association.said
            assert assess_association(rgy=runtime.rgy, said=association.said,
                                      schema_said=schema_said,
                                      trusted_geda_aid=geda.pre).outcome == "invalid"
            print("Revoked LE blocks new acceptance and fresh verification", flush=True)
            if comply is not None:
                while any(runtime.baser.get_managed(said).state != "withdrawn" for said in target_saids):
                    assert monotonic() - revocation_started < 30, "source withdrawal exceeded local 30s bound"
                    yield 0.01
                # All independent receivers and the local service have now
                # confirmed withdrawal. Later balance/query checks do not add
                # propagation latency; they independently validate the effects.
                propagation["sourceRevocationSeconds"] = round(monotonic() - revocation_started, 3)
                assert propagation["sourceRevocationSeconds"] < 30, propagation
                for wallet in (account.address, beneficiary.address):
                    _, endorsement = yield from fixture_effects.observe(wallet)
                    assert endorsement == ("0x" + "00" * 32, "")
                assert (yield from read(fixture_effects, wrapper, "transfer", [beneficiary.address, 1000000], sender=account.address)) == (False,)
                yield from send(fixture_effects, wrapper, "transfer", [beneficiary.address, 1000000], PUBLIC_TEST_KEY)
                assert (yield from read(fixture_effects, token, "balanceOf", [beneficiary.address])) == (1000000,)
                print("Observed source revocation withdraws endorsements and rejects transfer", flush=True)
                if process_check:
                    for result in (posted_result, broadcast_result):
                        _, endorsement = yield from fixture_effects.observe(account.address.lower())
                        assert endorsement == ("0x" + "00" * 32, "")
                    print("Independent posting process propagates observed source revocation", flush=True)
            if two_chain_check:
                assert (yield from second_effects.observe(account.address))[1] == ("0x" + "00" * 32, "")
                print("Both chains propagate observed source revocation independently", flush=True)

            # Commit success only after all assertions; a later timeout must fail.
            final_operations.extend([{"associationSaid": op.associationSaid, "state": op.state,
                                      "transactions": runtime.baser.transaction_status(op.associationSaid), "failureReason": op.failureReason}
                                     for _, op in runtime.baser.managed.getItemIter()])
            outcome.update(verified)
            workflow.remove(doers)
            return True

        workflow = doing.DoDoer(doers=[*doers, doing.doify(run)])
        doing.Doist(real=True, tock=0.01, limit=360.0 if comply else 120.0).do(doers=[workflow])
        assert outcome, "Issuance/HTTP exercise exceeded its time limit"
        print(
            json.dumps(
                {"geda": geda.pre, "qvi": qvi.pre, "le": le.pre,
                 "initialVerification": outcome, "localPropagation": propagation,
                 "finalOperations": final_operations},
                indent=2,
            )
        )
        print(
            "PASS: delegated GEDA -> QVI -> LE -> association verified through IPEX/HTTP"
        )


def main() -> None:
    """Select schemas and optional store retention, then run the exercise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema-dir",
        type=Path,
        required=True,
        help="vLEI checkout schema/acdc directory",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="retain stores under a fresh KLI base and print inspection commands",
    )
    parser.add_argument("--comply-dir", type=Path, help="Extend through fresh Forge deployment, posting, and transfers")
    parser.add_argument("--process-check", action="store_true", help="Qualify crash/restart through independent oracle CLI processes")
    parser.add_argument("--kli-check", action="store_true", help="Qualify KERIpy CLI issuance; retains fresh test stores")
    parser.add_argument("--two-chain-check", action="store_true", help="Qualify actual signed-chain isolation through two deployments")
    args = parser.parse_args()
    if args.two_chain_check and args.comply_dir is None:
        parser.error("--two-chain-check requires --comply-dir")
    exercise(args.schema_dir, keep=args.keep, comply=args.comply_dir,
             process_check=args.process_check, kli_check=args.kli_check,
             two_chain_check=args.two_chain_check)


if __name__ == "__main__":
    main()

"""Issue a delegated vLEI chain and exercise the oracle through IPEX/HTTP.

Uses the KLI's Registrar and Credentialer under one HIO scheduler. GEDA, QVI,
LE, and oracle have separate KERI stores (temporary unless --keep is set).
Identifiers are single-sig and witness-free; the local GEDA is a test trust root.
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
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ceremony import association_from_challenge, write_credential
from eth_account import Account
from eth_account.messages import encode_typed_data
from hio.base import doing
from hio.core import http
from issuance_helpers import (
    IssuerEscrows,
    anchor,
    grant,
    open_issuers,
    receive,
    replay,
    rules,
)
from keri.app import httping
from keri.core import coring, scheming

from vlei_oracle.association_verifier import verify_association
from vlei_oracle.constants import LE_SCHEMA, QVI_SCHEMA
from vlei_oracle.eoa import (
    build_challenge,
    normalize_ext_id,
)
from vlei_oracle.errors import VerificationError
from vlei_oracle.serving import ServiceConfig, setup_service

ROOT = Path(__file__).resolve().parents[2]
ASSOCIATION_SCHEMA = ROOT / "protocol/external-association-v1/association.schema.json"
PUBLIC_TEST_KEY = "0x" + "0" * 63 + "1"


def free_port() -> int:
    """Choose an available loopback port for a service started by this exercise."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PresentationDoer(doing.DoDoer):
    """Deliver CESR evidence and poll one oracle operation to an expected state.

    Owns its HTTP client and yields while transport and server processing run.
    HTTP acceptance confirms delivery; the status response confirms verification.
    ``result`` is populated only after the expected state is observed.
    """

    def __init__(self, *, port, recipient, stream, said, expected):
        self.client = http.Client(hostname="127.0.0.1", port=port)
        self.client_doer = http.ClientDoer(client=self.client)
        self.recipient, self.stream = recipient, stream
        self.said, self.expected = said, expected
        self.result = None
        super().__init__(doers=[self.client_doer, doing.doify(self.run)])

    def run(self, tymth, tock=0.0, **opts):
        """Send all messages, consume their responses, then poll with fresh headers."""
        yield tock
        count = httping.streamCESRRequests(
            self.client, bytearray(self.stream), self.recipient
        )
        for _ in range(count):
            while not self.client.responses:
                yield 0.01
            response = self.client.responses.popleft()
            assert response["status"] in (200, 202, 204), response
        for _ in range(100):
            # HIO retains headers by default; GET must drop the CESR POST headers.
            self.client.request(
                method="GET", path=f"/presentations/{self.said}", headers={}
            )
            while not self.client.responses:
                yield 0.01
            response = self.client.responses.popleft()
            result = json.loads(response["body"])
            if response["status"] == 200:
                if result["state"] == self.expected:
                    self.result = result
                    self.remove([self.client_doer])
                    return True
                assert result["state"] not in ("failed", "verified"), result
            yield 0.05
        raise AssertionError(f"Expected {self.expected}: {result}")


def exercise(schema_dir: Path, *, keep: bool = False) -> None:
    """Set up isolated actors, then schedule the complete issuance/HTTP workflow.

    ``schema_dir`` supplies QVI and LE schemas; this checkout supplies the
    association schema. ExitStack owns actor stores and the Anvil subprocess.
    One Doist schedules issuance, transport, oracle verification, and assertions.
    ``keep`` retains stores under a fresh KLI-compatible base, even on failure;
    it does not keep the HTTP service or Anvil running.
    """
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
        actors, issuers = open_issuers(stack, schemas, base=base, temp=temp)
        _geda_hby, geda, _geda_rgy = actors["geda"]
        _qvi_hby, qvi, qvi_rgy = actors["qvi"]
        le_hby, le, le_rgy = actors["le"]

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
        doers = [
            *runtime.doers,
            http.ClientDoer(client=rpc),
            IssuerEscrows(list(issuers.values())),
        ]
        for issuer in issuers.values():
            doers.extend([issuer.registrar, issuer])
        outcome = {}

        def run(tymth, tock=0.0, **opts):
            """Advance explicit workflow steps while native HIO children do the work."""
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
                issuance = registries[name].issue(
                    said=creder.said, dt=creder.attrib["dt"]
                )
                anc = anchor(registries[name].hab, issuance)
                issuer.issue(creder, issuance)
                issuer.registrar.issue(creder, issuance, anc)
                while not issuer.complete(creder.said):
                    yield 0.01
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
            for label, key, chain_id in cases:
                nonce = str(uuid4())
                ext_id = normalize_ext_id(chain_id, account.address)
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
                        nonce_store=Path(le_rgy.reger.path)
                        / "association-nonces.sqlite3",
                        output=artifacts / f"{label}.json",
                    )
                issuer = issuers["le"]
                issuer.validate(creder)
                issuance = registries["le"].issue(said=creder.said)
                anc = anchor(le, issuance)
                issuer.issue(creder, issuance)
                issuer.registrar.issue(creder, issuance, anc)
                while not issuer.complete(creder.said):
                    yield 0.01
                associations[label] = creder

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
                assert not check.result["verified"] and not check.result["txHashes"]
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

            # 10. Deliver the chain and assert the independently verified wallet binding.
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
            assert verified["verified"]["wallet"] == account.address.lower(), verified
            assert verified["verified"]["associationSaid"] == association.said, verified
            assert not verified["txHashes"], verified
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
                assert not check.result["txHashes"], check.result
                if expected == "failed":
                    assert not check.result["verified"], check.result
            print("Signer, chain, contract, and EIP-7702 cases passed", flush=True)

            # 12. Revoke LE through Registrar, then present a previously withheld child.
            revocation = registries["qvi"].revoke(said=le_credential.said)
            issuers["qvi"].registrar.revoke(
                le_credential, revocation, anchor(qvi, revocation)
            )
            while not issuers["qvi"].registrar.complete(
                le_credential.said, sn=revocation.sn
            ):
                yield 0.01
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
            assert not check.result["verified"] and not check.result["txHashes"], (
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
            print("Revoked LE blocks new acceptance and fresh verification", flush=True)

            # Commit success only after all assertions; a later timeout must fail.
            outcome.update(verified)
            workflow.remove(doers)
            return True

        workflow = doing.DoDoer(doers=[*doers, doing.doify(run)])
        doing.Doist(real=True, tock=0.01, limit=120.0).do(doers=[workflow])
        assert outcome, "Issuance/HTTP exercise exceeded its time limit"
        print(
            json.dumps(
                {"geda": geda.pre, "qvi": qvi.pre, "le": le.pre, "result": outcome},
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
    args = parser.parse_args()
    exercise(args.schema_dir, keep=args.keep)


if __name__ == "__main__":
    main()

"""Issue deliberately invalid associations over genuine source/TEL evidence."""

from copy import deepcopy
from uuid import uuid4

from eth_account import Account
from eth_account.messages import encode_typed_data
from keri.app import habbing, httping, oobiing, signing
from keri.core import coring, serdering
from keri.db import basing
from keri.help import helping
from keri.vdr import credentialing

from ceremony import association_from_challenge
from issuance_helpers import (
    IssuerEscrows, anchor, collect_receipts, grant, issue_credential, issuer_for,
    receive, replay, rules,
)
from transport_fixture import PresentationDoer
from vlei_oracle.constants import LE_SCHEMA, QVI_SCHEMA
from vlei_oracle.eoa import build_challenge, normalize_ext_id

KEY = "0x" + "0" * 63 + "1"


def association(hab, registry, le_said):
    challenge = build_challenge(
        issuer_aid=hab.pre, le_vlei_said=le_said,
        ext_id=normalize_ext_id(31337, Account.from_key(KEY).address), nonce=str(uuid4()),
    )
    signature = Account.sign_message(encode_typed_data(full_message=challenge), KEY).signature
    return association_from_challenge(challenge, "0x" + signature.hex(), registry.regk)


def issue_invalid(registrar, hab, registry, creder):
    """Witness a deliberately invalid test artifact without synthetic acceptance."""
    issuance = registry.issue(said=creder.said)
    registrar.issue(creder, issuance, anchor(hab, issuance))
    while not registrar.complete(creder.said):
        yield 0.01
    return signing.serialize(
        creder, coring.Prefixer(qb64=issuance.pre),
        coring.Seqner(sn=issuance.sn), coring.Saider(qb64=issuance.said),
    )


def qualify_relationships(*, actors, issuers, registries, credentials, schemas,
                          streams, runtime, oracle_port, workflow, stack,
                          base, temp, witness, witness_url):
    """Check invalid holder/issuer, edge, schema and obsolete targeted material."""
    _, qvi, qvi_rgy = actors["qvi"]
    _, le, le_rgy = actors["le"]
    # This accepted LE credential belongs to QVI's AID, not to the test LE.
    # Its source chain is valid; copying the LEI cannot authorize the other AID.
    source = coring.Saider.saidify(sad={
        "d": "", "qvi": {"n": credentials["geda"].said, "s": QVI_SCHEMA},
    })[1]
    unrelated = issuers["qvi"].create(
        regname="qvi", recp=qvi.pre, schema=LE_SCHEMA, source=source,
        rules=rules(schemas[LE_SCHEMA]), data={"LEI": credentials["qvi"].attrib["LEI"]},
    )
    yield from issue_credential(issuers["qvi"], registries["qvi"], unrelated)
    receive(runtime.hby, runtime.rgy, streams["geda"]
            + replay(qvi, qvi_rgy, registries["qvi"], unrelated))
    assert runtime.rgy.reger.saved.get(keys=(unrelated.said,))
    # Genuine non-delegated QVI: its signed chain is generic-KERI valid, but the
    # application must reject the missing GEDA delegation relationship.
    aux_hby = stack.enter_context(habbing.openHby(name="nondelegated-qvi", base=base, temp=temp))
    for schema in schemas.values():
        aux_hby.db.schema.pin(keys=(schema.said,), val=schema)
    aux = aux_hby.makeHab(name="nondelegated-qvi", icount=1, isith="1", ncount=1,
                         nsith="1", wits=[witness.pre], toad=1)
    aux_rgy = credentialing.Regery(hby=aux_hby, name="nondelegated-qvi", base=base, temp=temp)
    stack.callback(aux_rgy.reger.close, clear=temp)
    aux_issuer = issuer_for(aux_hby, aux_rgy)
    oobi = f"{witness_url}/oobi/{witness.pre}/controller"
    aux_hby.db.oobis.pin(keys=(oobi,), val=basing.OobiRecord(date=helping.nowIso8601()))
    resolver = oobiing.Oobiery(hby=aux_hby, clienter=httping.Clienter())
    aux_doers = [*resolver.doers, aux_issuer.registrar, aux_issuer, IssuerEscrows([aux_issuer])]
    workflow.extend(aux_doers)
    while aux_hby.db.roobi.get(keys=(oobi,)) is None:
        yield 0.01
    assert aux_hby.db.roobi.get(keys=(oobi,)).state == oobiing.Result.resolved
    yield from collect_receipts(aux_issuer, aux, 0)
    aux_registry = aux_rgy.makeRegistry(name="nondelegated-qvi", prefix=aux.pre, noBackers=True)
    aux_issuer.registrar.incept(aux_registry.vcp, anchor(aux, aux_registry.vcp))
    while not aux_issuer.registrar.complete(aux_registry.regk):
        yield 0.01
    receive(actors["geda"][0], actors["geda"][2],
            b"".join(aux.db.clonePreIter(pre=aux.pre)))
    aux_qvi = issuers["geda"].create(
        regname="geda", recp=aux.pre, schema=QVI_SCHEMA, source=None,
        rules=rules(schemas[QVI_SCHEMA]), data={"LEI": credentials["geda"].attrib["LEI"]},
    )
    yield from issue_credential(issuers["geda"], registries["geda"], aux_qvi)
    geda_stream = replay(actors["geda"][1], actors["geda"][2], registries["geda"], aux_qvi)
    receive(aux_hby, aux_rgy, geda_stream)
    receive(aux_hby, aux_rgy, b"".join(le.db.clonePreIter(pre=le.pre)))
    aux_source = coring.Saider.saidify(sad={
        "d": "", "qvi": {"n": aux_qvi.said, "s": QVI_SCHEMA},
    })[1]
    aux_le = aux_issuer.create(
        regname="nondelegated-qvi", recp=le.pre, schema=LE_SCHEMA, source=aux_source,
        rules=rules(schemas[LE_SCHEMA]), data={"LEI": credentials["qvi"].attrib["LEI"]},
    )
    yield from issue_credential(aux_issuer, aux_registry, aux_le)
    receive(runtime.hby, runtime.rgy, geda_stream + replay(aux, aux_rgy, aux_registry, aux_le))
    assert runtime.rgy.reger.saved.get(keys=(aux_le.said,))
    assert not runtime.hby.kevers[aux.pre].delpre
    cases = [
        ("wrong_issuer", qvi, qvi_rgy, registries["qvi"],
         association(qvi, registries["qvi"], credentials["qvi"].said), "issuer does not match"),
        ("unrelated_holder", le, le_rgy, registries["le"],
         association(le, registries["le"], unrelated.said), "issuer does not match"),
        ("nondelegated_qvi", le, le_rgy, registries["le"],
         association(le, registries["le"], aux_le.said), "not delegated by the trusted GEDA"),
    ]
    for label, mutate, reason in (
        ("wrong_edge", lambda sad: sad["e"]["le"].update(o="DI2I"), "schema"),
        ("wrong_schema", lambda sad: sad.update(s=LE_SCHEMA), "association schema"),
        ("targeted", lambda sad: sad["a"].update(i=le.pre), "schema"),
        ("copied_lei", lambda sad: sad["a"].update(LEI=credentials["qvi"].attrib["LEI"]), "schema"),
    ):
        sad = deepcopy(association(le, registries["le"], credentials["qvi"].said).sad)
        mutate(sad)
        for block in ("a", "e"):
            sad[block] = coring.Saider.saidify(sad=sad[block])[1]
        cases.append((label, le, le_rgy, registries["le"],
                      serdering.SerderACDC(sad=sad, makify=True), reason))

    for label, hab, rgy, registry, creder, reason in cases:
        # Deliberate negative fixture: anchor genuine issuance, but do not ask
        # Credentialer to accept an artifact known to violate its source/schema.
        registrar = issuers["qvi" if hab.pre == qvi.pre else "le"].registrar
        acdc = yield from issue_invalid(registrar, hab, registry, creder)
        evidence = streams["geda"] + streams["qvi"]
        evidence += b"".join(hab.db.clonePreIter(pre=hab.pre))
        evidence += b"".join(rgy.reger.clonePreIter(pre=registry.regk))
        evidence += grant(hab, rgy, creder, runtime.hab.pre, acdc=acdc)
        check = PresentationDoer(
            port=oracle_port, recipient=runtime.hab.pre, stream=evidence,
            said=creder.said, expected="failed",
        )
        workflow.extend([check])
        while not check.done:
            yield 0.01
        workflow.remove([check])
        assert reason in check.result["failureReason"], (label, check.result)
        assert not check.result["verified"] and not check.result["txHashes"]
    print("Real source/TEL negatives reject issuer, unrelated holder, edge, schema, targeting, and copied LEI", flush=True)
    workflow.remove(aux_doers)

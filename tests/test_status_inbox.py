"""Trusted status receipt: durable ACKs, replay boundaries, and real sender recovery.

EOA Verifier is an installed, pinned test dependency only. The producer fixture
starts after initial verification; KERIpy credential verification remains covered
by that application's tests, not repeated by the oracle.
"""

import json
import socket
from types import SimpleNamespace

import pytest
from falcon import testing
from hio.base import doing
from keri.app import habbing
from keri.core import coring
from keri.db import dbing

from eoa_verifier.delivery.authing import ServiceAuthenticator as SenderAuthenticator
from eoa_verifier.delivery.delivering import WebhookSender
from eoa_verifier.persistence.basing import VerifierBaser
from eoa_verifier.persistence.records import PendingVerification, VerificationResult
from vlei_oracle.basing import OracleBaser
from vlei_oracle.receiving.service import ReceiverConfig, StatusReceiver, run_receiver


def said(label):
    """Make a canonical digest identifier for fixture facts, not a verified credential."""
    return coring.Diger(ser=label.encode()).qb64


@pytest.fixture
def peers(tmp_path):
    """Own KERIpy peer identities and separate persistent application stores."""
    with (
        habbing.openHby(name="producer", temp=True) as producer,
        habbing.openHby(name="oracle", temp=True) as oracle,
        dbing.openLMDB(
            cls=VerifierBaser, name="producer", headDirPath=str(tmp_path), temp=False
        ) as outbox,
        dbing.openLMDB(
            cls=OracleBaser, name="oracle", headDirPath=str(tmp_path), temp=False
        ) as inbox,
    ):
        producer_hab = producer.makeHab(name="producer")
        # Bootstrap through the receiver's actual KERIpy KEL import path.
        kel = tmp_path / "producer.cesr"
        kel.write_bytes(producer_hab.makeOwnEvent(sn=0))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = ReceiverConfig(
            verifier_aid=producer_hab.pre,
            chain_id=31337,
            webhook_url=f"http://127.0.0.1:{port}/v1/status-events",
            verifier_kel=str(kel),
            host="127.0.0.1",
            http_port=port,
        )
        receiver = StatusReceiver(config, hby=oracle, baser=inbox)
        introduction = testing.TestClient(receiver.app).simulate_get(
            f"/oobi/{receiver.hab.pre}"
        )
        assert introduction.status_code == 200
        producer.psr.parse(ims=bytearray(introduction.content))
        facts = {
            "issuerAid": producer_hab.pre,
            "leSaid": said("LE"),
            "extId": "eip155:31337:0x" + "11" * 20,
            "issuanceAnchor": {"sequenceNumber": 3, "said": said("issuanceAnchor")},
        }
        association = said("association")
        outbox.enqueue(PendingVerification(association, said("grant")))
        outbox.finish(
            VerificationResult(
                association,
                "verified",
                eoaResult={
                    "wallet": "0x" + "11" * 20,
                    "accountObservation": {"observedAt": "2000-01-01T00:00:00Z"},
                },
            )
        )
        accepted = outbox.accept(
            association,
            association=facts,
            nonce="inbox-fixture",
            destination=config.webhook_url,
            peer_aid=receiver.hab.pre,
        )
        event = outbox.outbox.get(keys=(accepted.eventId,))
        yield SimpleNamespace(
            config=config,
            receiver=receiver,
            oracle=oracle,
            producer=producer,
            hab=producer_hab,
            inbox=inbox,
            outbox=outbox,
            event=event,
            auth=SenderAuthenticator(
                hab=producer_hab, baser=outbox, peer_aid=receiver.hab.pre
            ),
        )


def post(peers, body, *, auth=None, headers=None):
    """Send exact bytes through Falcon, signing each application retry anew."""
    if headers is None:
        headers, _ = (auth or peers.auth).sign(
            method="POST",
            target=peers.config.webhook_url,
            action="status",
            body=body,
        )
    return testing.TestClient(peers.receiver.app).simulate_post(
        "/v1/status-events", body=body, headers=headers
    )


def test_status_commit_survives_reordering_reopen_and_conflict(peers):
    """Revocation arriving first stays retired; duplicate ACKs never revive or rewrite it."""
    accepted = json.loads(peers.event.body)
    revoked = {
        **accepted,
        "eventId": f"{accepted['associationSaid']}:2",
        "statusRevision": 2,
        "status": "revoked",
        "reasonCode": "REVOKED",
    }
    body = json.dumps(revoked).encode()
    assert post(peers, body).status_code == 200
    peers.inbox.close()
    peers.inbox.reopen()
    assert post(peers, peers.event.body.encode()).status_code == 200
    assert post(peers, body).status_code == 200
    status = peers.inbox.statuses.get(keys=(accepted["associationSaid"],))
    assert (status.status, status.statusRevision) == ("revoked", 2)
    assert status.wallet == "0x" + "11" * 20
    assert len(list(peers.inbox.inbox.getItemIter())) == 2

    # Same revision with changed facts is a conflict, even with a valid signature.
    revoked["association"] = {**revoked["association"], "leSaid": said("other LE")}
    assert post(peers, json.dumps(revoked).encode()).status_code == 409
    assert peers.inbox.statuses.get(keys=(accepted["associationSaid"],)) == status


def test_authentication_replay_and_bad_event_leave_no_new_status(peers):
    """Altered bytes, an unknown signer and replay cannot create inbox effects."""
    body = peers.event.body.encode()
    headers, _ = peers.auth.sign(
        method="POST", target=peers.config.webhook_url, action="status", body=body
    )
    assert post(peers, body + b" ", headers=headers).status_code == 401
    with habbing.openHby(name="stranger", temp=True) as hby:
        hab = hby.makeHab(name="stranger")
        stranger = SenderAuthenticator(
            hab=hab, baser=peers.outbox, peer_aid=peers.receiver.hab.pre
        )
        assert post(peers, body, auth=stranger).status_code == 401
    assert not list(peers.inbox.inbox.getItemIter())
    assert post(peers, b"{}").status_code == 400
    # The revised v1 consumer does not accept the former event vocabulary.
    obsolete = json.loads(body)
    obsolete["outcome"] = "valid"
    del obsolete["status"]
    assert post(peers, json.dumps(obsolete).encode()).status_code == 400
    response = post(peers, body, headers=headers)
    assert response.status_code == 200
    assert response.json == {"eventId": peers.event.eventId, "stored": True}
    peers.inbox.close()
    peers.inbox.reopen()
    assert post(peers, body, headers=headers).status_code == 401
    assert len(list(peers.inbox.inbox.getItemIter())) == 1


@pytest.mark.parametrize("gap", ["none", "before-ack", "before-sender-completion"])
def test_real_sender_recovers_commit_ack_gaps(peers, monkeypatch, gap):
    """Actual signed HTTP completes once, including automatic redelivery after either commit gap."""
    sender = WebhookSender(baser=peers.outbox, hab=peers.hab, timeout=1)

    def fail(*args, **kwargs):
        """Simulate process loss precisely at the selected durable/network boundary."""
        raise RuntimeError("simulated process loss")

    def drive(worker, *, expect_failure=False):
        """Run HIO HTTP and sender until one attempt settles, then close both sockets."""

        starting_attempts = peers.outbox.outbox.get(keys=(peers.event.eventId,)).attempts

        def stop_when_settled(tymth, tock=0.0, **kwa):
            yield tock
            while True:
                event = peers.outbox.outbox.get(keys=(peers.event.eventId,))
                if event.acknowledged or (
                    event.attempts > starting_attempts
                    and event.error
                    and event.error != "attempt started; authenticated ACK not recorded"
                ):
                    parent.remove([worker, peers.receiver])
                    return
                yield 0.01

        parent = doing.DoDoer(
            doers=[peers.receiver, worker, doing.doify(stop_when_settled)]
        )
        scheduler = doing.Doist(real=True, tock=0.01, limit=3)
        if expect_failure:
            with pytest.raises(RuntimeError, match="simulated process loss"):
                scheduler.do(doers=[parent])
        else:
            scheduler.do(doers=[parent])
            assert parent.done, "sender did not settle before test deadline"

    with monkeypatch.context() as patch:
        if gap == "before-ack":
            from vlei_oracle.authing import ServiceAuthenticator

            patch.setattr(ServiceAuthenticator, "sign", fail)
        elif gap == "before-sender-completion":
            patch.setattr(peers.outbox, "delivery_result", fail)
        drive(sender, expect_failure=(gap == "before-sender-completion"))

    # The receiver has committed even when the ACK/completion boundary fails.
    assert peers.inbox.inbox.get(keys=(peers.event.eventId,)) is not None
    if gap != "none":
        assert not peers.outbox.outbox.get(keys=(peers.event.eventId,)).acknowledged
        # Reopen both durable stores and construct fresh workers, as after restart.
        peers.outbox.close()
        peers.outbox.reopen()
        peers.inbox.close()
        peers.inbox.reopen()
        peers.receiver = StatusReceiver(
            peers.config, hby=peers.oracle, baser=peers.inbox
        )
        sender = WebhookSender(baser=peers.outbox, hab=peers.hab, timeout=1)
        drive(sender)
    acknowledged = peers.outbox.outbox.get(keys=(peers.event.eventId,))
    assert acknowledged.acknowledged
    assert acknowledged.attempts == (1 if gap == "none" else 2)
    ext_id = json.loads(peers.event.body)["association"]["extId"]
    assert peers.inbox.selected_association(ext_id).associationSaid == peers.event.associationSaid
    assert len(list(peers.inbox.inbox.getItemIter())) == 1
    status = peers.inbox.statuses.get(keys=(peers.event.associationSaid,))
    assert (
        status.status == "accepted"
    )  # The old observation does not expire acceptance.
    from eth_utils import keccak

    assert status.identityRef == "0x" + keccak(text=peers.hab.pre).hex()
    assert not list(peers.inbox.transactions.getItemIter())
    assert not peers.receiver.server.servant.opened


def test_receiver_reopens_identity_and_keripy_stores(peers, tmp_path, capsys):
    """The real entrypoint preserves its AID and closes its listener across restarts."""
    from dataclasses import replace

    config = replace(
        peers.config, head_dir=str(tmp_path / "service"), name="persistent-inbox"
    )
    with pytest.raises(FileNotFoundError):
        run_receiver(replace(config, verifier_kel=str(tmp_path / "missing.cesr")))
    run_receiver(config, expire=0.03)
    first = capsys.readouterr().out
    run_receiver(config, expire=0.03)
    assert capsys.readouterr().out == first


def test_issued_then_revoked_is_terminal_and_chain_scoped(peers):
    """Normal retirement advances status; a new acceptance cannot undo it or change chain."""
    accepted = json.loads(peers.event.body)
    assert post(peers, peers.event.body.encode()).status_code == 200
    revoked = {
        **accepted,
        "eventId": f"{accepted['associationSaid']}:2",
        "statusRevision": 2,
        "status": "revoked",
        "reasonCode": "REVOKED",
    }
    assert post(peers, json.dumps(revoked).encode()).status_code == 200
    revival = {**accepted, "eventId": f"{accepted['associationSaid']}:3", "statusRevision": 3}
    assert post(peers, json.dumps(revival).encode()).status_code == 409
    revival["association"] = {
        **accepted["association"],
        "extId": "eip155:1:0x" + "11" * 20,
    }
    assert post(peers, json.dumps(revival).encode()).status_code == 400
    assert (
        peers.inbox.statuses.get(keys=(accepted["associationSaid"],)).status
        == "revoked"
    )


def test_peer_rotation_requires_keripy_key_state_update(peers):
    """A newly signed duplicate is accepted only after KERIpy KEL rotation is imported."""
    body = peers.event.body.encode()
    assert post(peers, body).status_code == 200
    rotation = peers.hab.rotate()
    assert post(peers, body).status_code == 401
    # Operators supply the updated KERIpy stream; existing state accepts its successor.
    from pathlib import Path

    Path(peers.config.verifier_kel).write_bytes(rotation)
    peers.receiver = StatusReceiver(peers.config, hby=peers.oracle, baser=peers.inbox)
    assert post(peers, body).status_code == 200
    assert len(list(peers.inbox.inbox.getItemIter())) == 1


@pytest.mark.parametrize("method,path", [("POST", "/"), ("GET", "/associations/credential")])
def test_receiver_has_no_credential_intake_or_verification_api(peers, method, path):
    response = testing.TestClient(peers.receiver.app).simulate_request(path=path, method=method)
    assert response.status_code == 404

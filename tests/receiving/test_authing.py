"""receiving/test_authing.py."""

import json

import pytest
from keri.app import habbing

from tests.support.status_events import post
from vlei_oracle.receiving.authing import HttpSignatures as SenderAuthenticator
from vlei_oracle.receiving.keys import ServiceKeys
from vlei_oracle.runtime.serving import StatusReceiver


def test_signed_duplicate_and_bad_event_leave_no_new_status(peers):
    """Altered bytes and unknown signers fail; exact duplicates do not repeat effects."""
    body = peers.event.body.encode()
    headers = peers.auth.sign(method="POST", target=peers.config.webhook_url, body=body)
    assert post(peers, body + b" ", headers=headers).status_code == 401
    with habbing.openHby(name="stranger", temp=True) as hby:
        hab = hby.makeHab(name="stranger")
        stranger = SenderAuthenticator(
            keys=ServiceKeys(hab),
        )
        peers.oracle.psr.parse(ims=bytearray(hab.makeOwnEvent(sn=0)))
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
    assert (
        peers.auth.verify_optional(
            method="POST",
            target=peers.config.webhook_url,
            body=response.content,
            headers=response.headers,
        )
        == peers.receiver.hab.pre
    )
    peers.inbox.close()
    peers.inbox.reopen()
    assert post(peers, body, headers=headers).status_code == 200
    assert len(list(peers.inbox.inbox.getItemIter())) == 1
    assert peers.inbox.queued.cntAll() == 1


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
    assert peers.inbox.queued.cntAll() == 1


@pytest.mark.parametrize(
    "headers", [{}, {"signature": "invalid"}, {"signature-input": "invalid"}]
)
def test_oracle_requires_valid_request_signatures(peers, headers):
    """Oracle opts into producer authentication even though generic targets need not."""
    assert post(peers, peers.event.body.encode(), headers=headers).status_code == 401
    assert peers.inbox.inbox.cntAll() == 0
    assert peers.inbox.queued.cntAll() == 0

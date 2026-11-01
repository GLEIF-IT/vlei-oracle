"""receiving/test_authing.py."""

import json
from functools import partial

from keri.app import habbing

from tests.support.status_events import post
from vlei_oracle.persistence.transport import claim_nonce
from vlei_oracle.receiving.authing import ServiceAuthenticator as SenderAuthenticator
from vlei_oracle.runtime.serving import StatusReceiver


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
            hab=hab,
            claim_nonce=partial(claim_nonce, peers.outbox),
            peer_aid=peers.receiver.hab.pre,
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

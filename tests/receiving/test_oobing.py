"""OOBI availability and identity binding before status acceptance."""

from dataclasses import replace

from keri.app import habbing, oobiing

from tests.support.oobis import resolve_peer
from tests.support.status_events import post
from vlei_oracle.runtime.serving import StatusReceiver


def test_unresolved_verifier_oobi_is_retryable_without_event_storage(peers):
    """Retained keys do not bypass the fresh introduction requested at startup."""
    peers.receiver = StatusReceiver(peers.config, hby=peers.oracle, baser=peers.inbox)
    response = post(peers, peers.event.body.encode())
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert peers.inbox.inbox.cntAll() == peers.inbox.queued.cntAll() == 0


def test_other_resolved_aid_cannot_authorize_the_configured_verifier(peers):
    """A native resolved marker and a valid unrelated KEL cannot open the inbox."""
    with habbing.openHby(name="other", temp=True) as other:
        hab = other.makeHab(name="other")
        url = f"http://127.0.0.1:{peers.producer_port}/oobi/{hab.pre}"
        config = replace(peers.config, verifier_oobi=url)
        peers.receiver = StatusReceiver(config, hby=peers.oracle, baser=peers.inbox)
        record = resolve_peer(
            peers.oracle,
            url,
            other,
            peers.producer_port,
            resolver=peers.receiver.oobiery,
        )
        assert record.state == oobiing.Result.resolved
        assert hab.pre in peers.oracle.kevers
        assert not peers.receiver.verifier_oobi.ready()
        assert post(peers, peers.event.body.encode()).status_code == 503
        assert peers.inbox.inbox.cntAll() == peers.inbox.queued.cntAll() == 0

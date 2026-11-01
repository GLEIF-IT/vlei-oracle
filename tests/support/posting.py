"""Compose real posting services and drive their contract effects to completion."""

from dataclasses import replace

import pytest

from tests.support.contracts import KEY, observe
from vlei_oracle.endorsement.doing import PostingDoer
from vlei_oracle.runtime.serving import StatusReceiver


def posting_service_for(peers, deployment):
    """Use the actual receiver composition, with a dedicated chain writer."""
    rpc, addresses = deployment
    config = replace(
        peers.config,
        rpc_url=rpc,
        posting_key=KEY,
        endorsement_registry=addresses["oracleRegistry"],
        identity_registry=addresses["identityRegistry"],
        confirmations=1,
    )
    receiver = StatusReceiver(config, hby=peers.oracle, baser=peers.inbox)
    from tests.support.oobis import resolve_peer

    resolve_peer(
        peers.oracle,
        config.verifier_oobi,
        peers.producer,
        peers.producer_port,
        resolver=receiver.oobiery,
    )
    return next(
        doer for doer in receiver.doers if isinstance(doer, PostingDoer)
    ).service


def settle(postingService, wallet, expected_said):
    """Drive the ordinary worker until both its transaction receipt and actual mapping agree."""
    for _ in range(36):
        yield from postingService.cycle()
        _, endorsement = yield from observe(postingService.effects, wallet)
        if (
            endorsement[1] == expected_said
            and postingService.reader.next_pending() is None
            and postingService.reader.next_prep() is None
            and postingService.reader.next_queued() is None
        ):
            return endorsement
    pytest.fail(f"mapping did not settle to {expected_said}")

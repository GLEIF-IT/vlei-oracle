"""Shared peer identities and isolated real-contract deployment fixtures."""

import json
import os
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_account import Account
from keri.app import habbing
from keri.db import dbing

from scripts.integration.contracts import deploy
from tests.support.contracts import KEY
from tests.support.status_events import said
from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.receiving.authing import HttpSignatures as SenderAuthenticator
from vlei_oracle.receiving.keys import ServiceKeys
from vlei_oracle.runtime.config import ReceiverConfig
from vlei_oracle.runtime.serving import StatusReceiver

WALLET = Account.from_key("0x" + "0" * 63 + "1").address

IDENTITY = "0x" + "12" * 32


@pytest.fixture
def peers(tmp_path):
    """Own KERIpy peer identities and separate persistent application stores."""
    with (
        habbing.openHby(name="producer", temp=True) as producer,
        habbing.openHby(name="oracle", temp=True) as oracle,
        dbing.openLMDB(
            cls=OracleBaser, name="oracle", headDirPath=str(tmp_path), temp=False
        ) as inbox,
    ):
        producer_hab = producer.makeHab(name="producer")
        from scripts.integration.processes import free_port
        from tests.support.oobis import resolve_peer

        producer_port = free_port()
        verifier_oobi = f"http://127.0.0.1:{producer_port}/oobi/{producer_hab.pre}"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = ReceiverConfig(
            verifier_aid=producer_hab.pre,
            chain_id=31337,
            webhook_url=f"http://127.0.0.1:{port}/v1/status-events",
            verifier_oobi=verifier_oobi,
            host="127.0.0.1",
            http_port=port,
        )
        receiver = StatusReceiver(config, hby=oracle, baser=inbox)
        resolve_peer(
            oracle, verifier_oobi, producer, producer_port, resolver=receiver.oobiery
        )
        assert receiver.verifier_oobi.ready()
        resolve_peer(
            producer, f"http://127.0.0.1:{port}/oobi/{receiver.hab.pre}", oracle, port
        )
        facts = {
            "issuerAid": producer_hab.pre,
            "leSaid": said("LE"),
            "extId": "eip155:31337:0x" + "11" * 20,
            "issuanceAnchor": {"sequenceNumber": 3, "said": said("issuanceAnchor")},
        }
        association = said("association")
        event = SimpleNamespace(
            eventId=f"{association}:1",
            associationSaid=association,
            body=json.dumps(
                {
                    "version": "1",
                    "eventId": f"{association}:1",
                    "associationSaid": association,
                    "statusRevision": 1,
                    "status": "accepted",
                    "association": facts,
                    "accountObservation": {
                        "chainId": 31337,
                        "observedAt": "2000-01-01T00:00:00Z",
                    },
                }
            ),
        )
        yield SimpleNamespace(
            config=config,
            producer_port=producer_port,
            receiver=receiver,
            oracle=oracle,
            producer=producer,
            hab=producer_hab,
            inbox=inbox,
            event=event,
            auth=SenderAuthenticator(
                keys=ServiceKeys(producer_hab),
            ),
        )


@pytest.fixture(scope="module")
def comply_checkout(tmp_path_factory):
    """Expose the isolated contract inputs and outputs used by the deployment."""
    from scripts.integration.artifacts import snapshot_source

    selected = os.getenv("VLEI_COMPLY_DIR")
    assert selected, "Set VLEI_COMPLY_DIR to the exact qualified contract checkout"
    source = Path(selected).resolve()
    assert (source / "script/DeployAll.s.sol").is_file()
    isolated = tmp_path_factory.mktemp("contract-source") / "comply"
    snapshot_source(source, isolated)
    return isolated


@pytest.fixture(scope="module")
def deployed_chain(tmp_path_factory, comply_checkout):
    """Own Anvil and build/deploy the shared isolated Comply fixture checkout."""
    from scripts.integration.chain import start_chain
    from scripts.integration.processes import free_port, stop

    anvil = shutil.which("anvil")
    assert anvil and shutil.which("forge"), "Foundry 1.5.1 is required"
    root = tmp_path_factory.mktemp("contracts").resolve()
    (root / "logs").mkdir()
    port = free_port()
    configuration = {
        "anvil": anvil,
        "rpcPort": port,
        "rpcUrl": f"http://127.0.0.1:{port}",
    }
    try:
        start_chain(root, configuration)
        yield (
            configuration["rpcUrl"],
            deploy(
                comply_checkout, configuration["rpcUrl"], root / "contracts.json", KEY
            ),
        )
    finally:
        stop(root)


@pytest.fixture
def deployment(deployed_chain):
    """Give each fresh Oracle store the same known initial contract deployment.

    The module owns Anvil/build; snapshots isolate tests without changing topology.
    No test may depend on a previous test's registry writes or writer nonce.
    """
    from tests.support.contracts import drive, effects_for

    def checkpoint(effects):
        return (yield from effects.request("evm_snapshot", []))

    snapshot = drive(effects_for(deployed_chain), checkpoint)
    try:
        yield deployed_chain
    finally:

        def restore(effects):
            assert (yield from effects.request("evm_revert", [snapshot])) is True
            return True

        drive(effects_for(deployed_chain), restore)

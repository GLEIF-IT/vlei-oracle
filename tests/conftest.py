"""conftest.py."""

import json
import os
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_account import Account
from falcon import testing
from keri.app import habbing
from keri.db import dbing

from scripts.integration.artifacts import source_revision
from scripts.integration.contracts import deploy
from tests.support.contracts import KEY
from tests.support.status_events import said
from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.receiving.authing import ServiceAuthenticator as SenderAuthenticator
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
            cls=OracleBaser, name="producer", headDirPath=str(tmp_path), temp=False
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
    from scripts.integration.environment import start_chain
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


@pytest.fixture
def integration_run(tmp_path):
    """Attach an explicit started run, or own a fresh installed-wheel stack.

    The external run is managed by the operator command. A pytest-created run
    closes all child processes even when qualification fails, retaining evidence.
    """
    from scripts.integration.environment import (
        load_run,
        prepare_run,
        start_run,
        stop_run,
    )

    selected = os.getenv("ORACLE_INTEGRATION_RUN")
    if selected:
        root = Path(selected).resolve()
        load_run(root)
        yield root
        return
    wheel = os.environ.get("ORACLE_WHEEL")
    assert wheel and Path(wheel).is_file(), (
        "Set ORACLE_WHEEL to the fresh qualified wheel"
    )
    root = (tmp_path / "stack").resolve()
    source = Path(__file__).resolve().parents[1]
    prepare_run(
        root,
        source,
        verifier_source=Path(os.environ["VERIFIER_TOOL_SOURCE"])
        if os.getenv("VERIFIER_TOOL_SOURCE")
        else None,
        comply_source=Path(os.environ["VLEI_COMPLY_DIR"])
        if os.getenv("VLEI_COMPLY_DIR")
        else None,
        witness_count=1,
        wheel=Path(wheel),
        tool_revision=os.getenv("GITHUB_SHA") or source_revision(source),
    )
    try:
        start_run(root, load_run(root))
        yield root
    finally:
        stop_run(root)

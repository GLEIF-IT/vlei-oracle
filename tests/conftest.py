"""conftest.py."""

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from eoa_verifier.delivery.authing import ServiceAuthenticator as SenderAuthenticator
from eoa_verifier.persistence.basing import VerifierBaser
from eoa_verifier.persistence.records import PendingVerification, VerificationResult
from eth_account import Account
from falcon import testing
from keri.app import habbing
from keri.db import dbing

from tests.support.contracts import KEY, drive
from tests.support.status_events import said
from vlei_oracle.endorsement.effects import ContractEffects
from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.runtime.config import ReceiverConfig
from vlei_oracle.runtime.deploy_log import parse_deploy_log
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


@pytest.fixture(scope="module")
def deployment():
    """Deploy the pinned comply contracts on a test-owned Anvil process."""
    comply = Path(
        os.getenv(
            "VLEI_COMPLY_DIR", Path(__file__).resolve().parents[2] / "vlei-comply"
        )
    )
    forge, anvil = shutil.which("forge"), shutil.which("anvil")
    if not forge or not anvil or not (comply / "script/DeployAll.s.sol").is_file():
        pytest.fail("contract tests require Foundry and VLEI_COMPLY_DIR")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    rpc = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [anvil, "--host", "127.0.0.1", "--port", str(port), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail("Anvil did not open its RPC listener")
                time.sleep(0.02)
        readiness = ContractEffects(
            rpc_url=rpc,
            chain_id=31337,
            writer=Account.from_key(KEY).address,
            endorsement=WALLET,
            identity=WALLET,
        )
        drive(readiness, lambda effects: effects.check_chain())
        deployed = subprocess.run(
            [
                forge,
                "script",
                "script/DeployAll.s.sol",
                "--rpc-url",
                rpc,
                "--broadcast",
                "--non-interactive",
            ],
            cwd=comply,
            env={**os.environ, "PRIVATE_KEY": KEY},
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert deployed.returncode == 0, deployed.stdout + deployed.stderr
        yield rpc, parse_deploy_log(deployed.stdout)
    finally:
        process.terminate()
        process.wait(timeout=10)

"""Real witnessed two-service qualification and the maintained local USDC flow.

The Verifier's public fixture CLI owns issuance/presentation. This scenario owns
only contract setup and assertions against public HTTP and mined chain state.
"""

import json
from pathlib import Path
from uuid import uuid4

from eth_account import Account
from hio.base import doing

from scripts.integration.artifacts import load_json, save_json
from scripts.integration.environment import (
    restart_oracle,
)
from scripts.integration.processes import stop
from scripts.integration.verifier import call_verifier, start_verifier
from tests.support.contracts import KEY, contract, effects_for, observe, read, send
from tests.support.processes import request


class USDCScenario:
    """Borrow one started run and its HIO contract client for a fresh USDC flow."""

    def __init__(self, root: Path):
        """Bind explicit run artifacts, public fixture wallets and contract ABIs."""
        self.root = root
        self.configuration = load_json(root / "run.json")
        self.verifier = load_json(root / "verifier/run.json")
        self.addresses = self.configuration["contracts"]
        self.effects = effects_for((self.configuration["rpcUrl"], self.addresses))
        comply = Path(self.configuration["comply"])
        self.admin = contract(comply, "AdminControls", self.addresses["adminControls"])
        self.token = contract(comply, "USDC", self.addresses["usdcToken"])
        self.wrapper = contract(comply, "wUSDC", self.addresses["wrapper"])
        self.keys = {n: "0x" + "0" * 63 + str(n) for n in (1, 2, 3)}
        self.wallet = Account.from_key(self.keys[1]).address
        self.recipient = Account.from_key(self.keys[2]).address
        self.finished = False
        self.workflow = doing.DoDoer(doers=[self.effects, doing.doify(self.run)])

    def run(self, tymth, tock=0.0, **kwargs):
        """Sequence independent setup, acceptance, restart and revocation phases."""
        yield tock
        yield from self.prepare_wallets()
        first = self.issue()
        associationStatus = yield from self.status(first["said"], "accepted")
        assert associationStatus["association"]["issuerAid"] == first["issuerAid"]
        assert associationStatus["accountObservation"]["chainId"] == 31337
        yield from self.mapping(first["said"])
        yield from self.reject_nonce_reuse(first)
        beneficiary = self.issue(wallet=2)
        yield from self.status(beneficiary["said"], "accepted")
        yield from self.mapping(beneficiary["said"], self.recipient)
        yield from self.transfer(expected=True)
        self.assert_retry(first, associationStatus)
        yield from self.reopen(first, associationStatus)
        second = self.issue()
        yield from self.status(second["said"], "accepted")
        yield from self.mapping(second["said"])
        yield from self.revoke_associations(first, second, beneficiary)
        save_json(
            self.root / "flow.json",
            {
                "first": first,
                "second": second,
                "beneficiary": beneficiary,
                "accountObservation": associationStatus["accountObservation"],
            },
        )
        self.finished = True
        self.workflow.remove([self.effects])

    def prepare_wallets(self):
        """Use a separate administrator; prove no transfer before both endorsements."""
        yield from self.effects.request(
            "anvil_setBalance", [self.wallet, "0x56bc75e2d63100000"]
        )
        posting_wallet = Account.from_key(self.keys[3]).address
        role = self.effects.web3.keccak(text="COMPLIANCE_ADMIN_ROLE")
        assert (
            yield from read(self.effects, self.admin, "hasRole", [role, posting_wallet])
        ) == (True,)
        yield from send(self.effects, self.token, "mint", [self.wallet, 100000000], KEY)
        yield from send(
            self.effects,
            self.token,
            "approve",
            [self.wrapper.address, 100000000],
            self.keys[1],
        )
        yield from self.transfer(expected=False, initial=True)

    def issue(self, *, wallet=1, nonce=None):
        """Issue and present real witnessed bytes using the supported fixture CLI."""
        descriptor = call_verifier(
            self.root,
            "issue",
            "--wallet",
            str(wallet),
            "--nonce",
            nonce or str(uuid4()),
            descriptor=True,
        )
        call_verifier(self.root, "present", "--said", descriptor["said"])
        return descriptor

    def status(self, said, expected):
        """Wait for stored public status and its own authenticated ACK when emitted."""
        deadline = self.workflow.tyme + 25
        while True:
            code, body = yield from request(
                self.workflow,
                self.verifier["verifierPort"],
                f"/v1/associations/{said}/status",
            )
            value = json.loads(body)
            delivery = value.get("delivery")
            if (
                code == 200
                and value["status"] == expected
                and (expected == "rejected" or (delivery and delivery["delivered"]))
            ):
                return value
            assert self.workflow.tyme < deadline, (value, str(self.root / "logs"))
            yield 0.05

    def mapping(self, said, address=None):
        """Require actual canonical registry state, beyond an HTTP ACK or tx hash."""
        deadline = self.workflow.tyme + 25
        while True:
            _, current = yield from observe(self.effects, address or self.wallet)
            if current[1] == said:
                if said:
                    yield from self.endorsement(said, expected="accepted")
                return current
            assert self.workflow.tyme < deadline, (
                current,
                str(self.root / "logs/oracle.log"),
            )
            yield 0.05

    def endorsement(self, said, *, expected):
        """Observe terminal delivery through Oracle's public, local-only resource."""
        deadline = self.workflow.tyme + 25
        while True:
            code, body = yield from request(
                self.workflow,
                self.configuration["oraclePort"],
                f"/v1/endorsements/{said}",
            )
            value = json.loads(body)
            if code == 200 and value["deliveryStatus"] == "completed":
                assert set(value) == {
                    "associationSaid",
                    "wallet",
                    "publicationStatus",
                    "action",
                    "deliveryStatus",
                    "error",
                }
                assert value["associationSaid"] == said
                assert value["action"] == (
                    "publish" if expected == "accepted" else "withdraw"
                )
                assert value["publicationStatus"] == (
                    "published" if expected == "accepted" else "not_published"
                )
                assert value["error"] == ""
                return value
            assert self.workflow.tyme < deadline, value
            yield 0.05

    def events(self):
        """Read exact forwarded bytes retained by the isolated lost-ACK fixture."""
        return [
            json.loads(line)["body"]
            for line in (self.root / "proxy-events.jsonl").read_text().splitlines()
        ]

    def reject_nonce_reuse(self, first):
        """A valid second wallet reusing the accepted nonce creates no Oracle effect."""
        duplicate = self.issue(wallet=2, nonce=first["nonce"])
        assert duplicate["said"] != first["said"]
        rejected = yield from self.status(duplicate["said"], "rejected")
        assert rejected["reason"] == "NONCE_REUSED" and rejected["delivery"] is None
        assert not any(
            json.loads(body)["associationSaid"] == duplicate["said"]
            for body in self.events()
        )
        yield from self.mapping(first["said"])

    def transfer(self, *, expected, initial=False):
        """Check return value, mined execution and exact underlying USDC balances."""
        result = yield from read(
            self.effects,
            self.wrapper,
            "transfer",
            [self.recipient, 1000000],
            sender=self.wallet,
        )
        assert result == (expected,)
        yield from send(
            self.effects,
            self.wrapper,
            "transfer",
            [self.recipient, 1000000],
            self.keys[1],
        )
        expectedRecipientBalance = 0 if initial else 1000000
        assert (
            yield from read(self.effects, self.token, "balanceOf", [self.recipient])
        ) == (expectedRecipientBalance,)
        assert (
            yield from read(self.effects, self.token, "balanceOf", [self.wallet])
        ) == (100000000 - expectedRecipientBalance,)

    def assert_retry(self, first, associationStatus):
        """Loss after Oracle commit retries the identical event with no new identity."""
        assert (self.root / "dropped-ack").exists()
        assert associationStatus["delivery"]["attempts"] >= 2
        initial = [
            body
            for body in self.events()
            if json.loads(body)["eventId"] == f"{first['said']}:1"
        ]
        assert len(initial) >= 2 and len(set(initial)) == 1

    def reopen(self, first, associationStatus):
        """Reopen Oracle with Verifier offline, then verify durable producer state."""
        call_verifier(self.root, "stop", "--service", "verifier")
        stop(self.root, "oracle")
        restart_oracle(self.root, self.configuration)
        yield from self.mapping(first["said"])
        start_verifier(self.root, self.configuration)
        reopened = yield from self.status(first["said"], "accepted")
        assert reopened["accountObservation"] == associationStatus["accountObservation"]
        call_verifier(self.root, "present", "--said", first["said"])
        assert (yield from self.status(first["said"], "accepted"))[
            "eventId"
        ] == associationStatus["eventId"]

    def revoke_associations(self, first, second, beneficiary):
        """Old revocation preserves replacement; current revocation removes only it."""
        for descriptor, expected in ((first, second["said"]), (second, "")):
            call_verifier(self.root, "revoke", "--said", descriptor["said"])
            call_verifier(self.root, "present", "--said", descriptor["said"])
            revokedStatus = yield from self.status(descriptor["said"], "revoked")
            assert revokedStatus["delivery"]["eventId"] == f"{descriptor['said']}:2"
            yield from self.mapping(expected)
            yield from self.endorsement(descriptor["said"], expected="revoked")
        yield from self.mapping(beneficiary["said"], self.recipient)
        yield from self.transfer(expected=False)


def test_two_installed_services_publish_replace_and_revoke(integration_run):
    """Keep the full witnessed, two-wallet, lost-ACK and restart qualification."""
    marker = integration_run / "flow-started"
    with marker.open("x") as stream:
        stream.write("A full flow requires fresh scenario state.\n")
    scenario = USDCScenario(integration_run)
    doing.Doist(real=True, tock=0.01, limit=180).do(doers=[scenario.workflow])
    assert scenario.finished, str(integration_run / "logs")

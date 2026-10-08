import pytest

from vlei_oracle.posting import ZERO_IDENTITY, next_action

WALLET = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
IDENTITY = "0x" + "12" * 32
OTHER = "0x" + "34" * 32
BINDING = {"wallet": WALLET, "identityRef": IDENTITY, "associationSaid": "new"}


@pytest.mark.parametrize(
    "identity,endorsement,expected",
    [
        (ZERO_IDENTITY, (ZERO_IDENTITY, ""),
         ("identity", "registerIdentity", [IDENTITY, WALLET, b""])),
        (IDENTITY, (ZERO_IDENTITY, ""),
         ("endorsement", "publishEndorsement", [WALLET, IDENTITY, "new"])),
        (IDENTITY, (IDENTITY, "old"),
         ("endorsement", "publishEndorsement", [WALLET, IDENTITY, "new"])),
        (IDENTITY, (IDENTITY, "new"), None),
        (OTHER, (OTHER, "old"),
         ("endorsement", "withdrawEndorsement", [WALLET, "old"])),
        (OTHER, (ZERO_IDENTITY, ""),
         ("identity", "removeIdentity", [OTHER, WALLET, b""])),
        (OTHER, (IDENTITY, "new"),
         ("identity", "removeIdentity", [OTHER, WALLET, b""])),
    ],
)
def test_reconcile_binding_in_safe_order(identity, endorsement, expected):
    assert next_action(
        BINDING, identity=identity, endorsement=endorsement, valid=True
    ) == expected


@pytest.mark.parametrize("current", ["new", "replacement", ""])
def test_invalid_evidence_withdraws_only_its_exact_association(current):
    action = next_action(
        BINDING, identity=IDENTITY, endorsement=(IDENTITY, current), valid=False
    )
    assert action == (
        ("endorsement", "withdrawEndorsement", [WALLET, "new"])
        if current == "new" else None
    )

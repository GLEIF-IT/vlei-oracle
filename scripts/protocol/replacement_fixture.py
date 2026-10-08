"""Native live replacement and late revocation against deployed contracts."""

from time import monotonic
from uuid import uuid4

from eth_account import Account
from eth_account.messages import encode_typed_data

from ceremony import write_credential
from issuance_helpers import grant, issue_credential, replay, revoke_credential
from transport_fixture import PresentationDoer
from vlei_oracle.verification.eoa import build_challenge, normalize_ext_id


def qualify_replacement(*, le, rgy, registry, issuer, le_said, previous,
                        key, runtime, port, effects, workflow, artifacts, ledger):
    """Replace a live endorsement; old native revocation must leave it intact."""
    wallet = Account.from_key(key).address
    assert (yield from effects.observe(wallet))[1][1] == previous.said
    challenge = build_challenge(issuer_aid=le.pre, le_vlei_said=le_said,
                                ext_id=normalize_ext_id(31337, wallet), nonce=str(uuid4()))
    signature = Account.sign_message(encode_typed_data(full_message=challenge), key).signature
    replacement = write_credential(
        challenge=challenge, signature="0x" + signature.hex(), registry=registry.regk,
        nonce_store=ledger, output=artifacts / "replacement.json",
    )
    yield from issue_credential(issuer, registry, replacement)
    started = monotonic()
    check = PresentationDoer(
        port=port, recipient=runtime.hab.pre,
        stream=replay(le, rgy, registry, replacement) + grant(le, rgy, replacement, runtime.hab.pre),
        said=replacement.said, expected="endorsed",
    )
    workflow.extend([check])
    while not check.done or runtime.baser.get_operation(previous.said).state != "withdrawn":
        assert monotonic() - started < 30, "live replacement exceeded local 30s bound"
        yield 0.01
    workflow.remove([check])
    assert (yield from effects.observe(wallet))[1][1] == replacement.said
    elapsed = round(monotonic() - started, 3)
    assert elapsed < 30
    yield from revoke_credential(issuer, registry, previous)
    check = PresentationDoer(
        port=port, recipient=runtime.hab.pre,
        stream=replay(le, rgy, registry, previous) + grant(le, rgy, previous, runtime.hab.pre),
        said=previous.said, expected="withdrawn",
    )
    workflow.extend([check])
    while not check.done:
        yield 0.01
    workflow.remove([check])
    assert runtime.rgy.reger.tevers[registry.regk].vcState(previous.said).et == "rev"
    assert (yield from effects.observe(wallet))[1][1] == replacement.said
    assert runtime.baser.get_operation(replacement.said).state == "endorsed"
    print(f"Live replacement took {elapsed:.3f}s; late old revocation preserves new endorsement", flush=True)
    return replacement, elapsed

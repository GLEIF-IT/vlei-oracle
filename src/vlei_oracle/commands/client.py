"""Sign a pre-issuance challenge; no signature attachment endpoint is needed."""

import json
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_typed_data

from vlei_oracle.association.eoa import build_challenge


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "client", help="External-controller ceremony helpers"
    )
    sub = parser.add_subparsers(dest="client_command", required=True)
    sign = sub.add_parser(
        "sign", help="Sign an association challenge with a local test key"
    )
    sign.add_argument("--challenge", required=True)
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--out", required=True)
    sign.set_defaults(handler=sign_handler)


def sign_handler(args):
    challenge = json.loads(Path(args.challenge).read_text())
    fields = challenge["message"]
    expected = build_challenge(
        issuer_aid=fields["issuerAid"],
        le_vlei_said=fields["leVleiSaid"],
        ext_id=fields["extId"],
        nonce=fields["nonce"],
    )
    if challenge != expected:
        raise ValueError("challenge does not match the EOA profile")
    signature = Account.sign_message(
        encode_typed_data(full_message=challenge), args.private_key
    ).signature
    Path(args.out).write_text("0x" + signature.hex() + "\n")

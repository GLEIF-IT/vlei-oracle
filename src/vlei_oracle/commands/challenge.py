"""Build the EOA challenge before association issuance."""

import json
from pathlib import Path

from vlei_oracle.verification.eoa import build_challenge, normalize_ext_id


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "challenge", help="Build association EIP-712 typed data"
    )
    parser.add_argument("--issuer-aid", required=True)
    parser.add_argument("--le-vlei-said", required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--out")
    parser.set_defaults(handler=handler)


def handler(args):
    challenge = build_challenge(
        issuer_aid=args.issuer_aid,
        le_vlei_said=args.le_vlei_said,
        ext_id=normalize_ext_id(args.chain_id, args.wallet),
        nonce=args.nonce,
    )
    raw = json.dumps(challenge, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(raw)
    else:
        print(raw, end="")

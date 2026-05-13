"""`vlei-oracle challenge` command."""

from __future__ import annotations

import time
from pathlib import Path

from vlei_oracle.wallet import build_typed_data, canonical_json


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("challenge", help="Build GL1 wallet EIP-712 challenge typed data")
    parser.add_argument("--legal-entity-aid", required=True)
    parser.add_argument("--lei", required=True)
    parser.add_argument("--oor-aid", required=True)
    parser.add_argument("--le-credential-said", required=True)
    parser.add_argument("--oor-credential-said", required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--vlei-token", required=True)
    parser.add_argument("--wrapper", required=True)
    parser.add_argument("--identity-registry", required=True)
    parser.add_argument("--credential-registry", required=True)
    parser.add_argument("--oracle-registry", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--issued-at", type=int, default=0)
    parser.add_argument("--expires-at", type=int, default=0)
    parser.add_argument("--purpose", default="GL1_WVLEI_TRANSFER")
    parser.add_argument("--out", help="Write typed data JSON to this file")
    parser.set_defaults(handler=handler)


def handler(args) -> None:
    issued_at = args.issued_at or int(time.time())
    expires_at = args.expires_at or issued_at + 3600
    typed_data = build_typed_data(
        legal_entity_aid=args.legal_entity_aid,
        lei=args.lei,
        oor_aid=args.oor_aid,
        le_credential_said=args.le_credential_said,
        oor_credential_said=args.oor_credential_said,
        wallet=args.wallet,
        chain_id=args.chain_id,
        vlei_token=args.vlei_token,
        wrapper=args.wrapper,
        identity_registry=args.identity_registry,
        credential_registry=args.credential_registry,
        oracle_registry=args.oracle_registry,
        nonce=args.nonce,
        expires_at=expires_at,
        purpose=args.purpose,
        issued_at=issued_at,
    )
    raw = canonical_json(typed_data) + "\n"
    if args.out:
        Path(args.out).write_text(raw)
    else:
        print(raw, end="")

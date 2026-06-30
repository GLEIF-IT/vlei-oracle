"""`vlei-oracle challenge` command."""

from __future__ import annotations

from pathlib import Path

from vlei_oracle.wallet import build_typed_data, canonical_json


def add_parser(subparsers) -> None:
    """Register the typed-data challenge builder command."""

    parser = subparsers.add_parser("challenge", help="Build GL1 wallet EIP-712 challenge typed data")
    parser.add_argument("--legal-entity-aid", required=True)
    parser.add_argument("--lei", required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--oracle-registry", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--expires-at", type=int, required=True)
    parser.add_argument("--out", help="Write typed data JSON to this file")
    parser.set_defaults(handler=handler)


def handler(args) -> None:
    """Build deterministic EIP-712 JSON used by the ACDC and wallet signer."""

    typed_data = build_typed_data(
        legal_entity_aid=args.legal_entity_aid,
        lei=args.lei,
        wallet=args.wallet,
        chain_id=args.chain_id,
        oracle_registry=args.oracle_registry,
        nonce=args.nonce,
        expires_at=args.expires_at,
    )
    raw = canonical_json(typed_data) + "\n"
    if args.out:
        Path(args.out).write_text(raw)
    else:
        print(raw, end="")

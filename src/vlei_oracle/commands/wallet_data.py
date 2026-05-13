"""`vlei-oracle wallet-data` command."""

from __future__ import annotations

import json
from pathlib import Path

from vlei_oracle.contracts import load_json_file
from vlei_oracle.wallet import wallet_attributes_from_typed_data


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("wallet-data", help="Build wallet attestation attributes from typed data")
    parser.add_argument("--typed-data", required=True)
    parser.add_argument("--out", required=True)
    parser.set_defaults(handler=handler)


def handler(args) -> None:
    typed_data = load_json_file(args.typed_data)
    attrs = wallet_attributes_from_typed_data(typed_data=typed_data)
    Path(args.out).write_text(json.dumps(attrs, indent=2, sort_keys=True) + "\n")

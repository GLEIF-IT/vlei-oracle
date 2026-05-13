"""`vlei-oracle parse-deploy-log` command."""

from __future__ import annotations

import json
from pathlib import Path

from vlei_oracle.contracts import parse_deploy_log


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("parse-deploy-log", help="Parse DeployAll stdout into contracts JSON")
    parser.add_argument("--log", required=True)
    parser.add_argument("--out", required=True)
    parser.set_defaults(handler=handler)


def handler(args) -> None:
    parsed = parse_deploy_log(Path(args.log).read_text())
    Path(args.out).write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n")

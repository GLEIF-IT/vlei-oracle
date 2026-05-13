"""`vlei-oracle server` command."""

from __future__ import annotations

from vlei_oracle.contracts import load_json_file
from vlei_oracle.serving import ServiceConfig, run_service


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("server", help="Run the always-on verifier/oracle service")
    sub = parser.add_subparsers(dest="server_command", required=True)
    start = sub.add_parser("start", help="Start the HIO/Falcon vLEI Oracle service")
    start.add_argument("--name", default="vlei-oracle")
    start.add_argument("--base", default="")
    start.add_argument("--alias", default="oracle")
    start.add_argument("--passcode")
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--http-port", type=int, default=9723)
    start.add_argument("--trusted-geda-aid", required=True)
    start.add_argument("--contracts")
    start.add_argument("--rpc-url", default="")
    start.add_argument("--private-key", default="")
    start.add_argument("--confirmations", type=int, default=0)
    start.add_argument("--no-post", action="store_true")
    start.add_argument("--initial-aid-allowed", action="store_true")
    start.add_argument("--incept-file")
    start.set_defaults(handler=start_handler)


def start_handler(args) -> None:
    contracts = load_json_file(args.contracts) if args.contracts else None
    run_service(
        ServiceConfig(
            name=args.name,
            base=args.base,
            alias=args.alias,
            passcode=args.passcode,
            host=args.host,
            http_port=args.http_port,
            trusted_geda_aid=args.trusted_geda_aid,
            contracts=contracts,
            rpc_url=args.rpc_url,
            private_key=args.private_key,
            confirmations=args.confirmations,
            no_post=args.no_post,
            initial_aid_allowed=args.initial_aid_allowed,
            incept_file=args.incept_file,
        )
    )

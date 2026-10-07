"""`vlei-oracle server` command."""

from __future__ import annotations

from vlei_oracle.serving import ServiceConfig, run_service


def add_parser(subparsers) -> None:
    """Register the always-on service command group."""

    parser = subparsers.add_parser(
        "server", help="Run the always-on verifier/oracle service"
    )
    sub = parser.add_subparsers(dest="server_command", required=True)
    start = sub.add_parser("start", help="Start the HIO/Falcon vLEI Oracle service")
    start.add_argument("--name", default="vlei-oracle")
    start.add_argument("--base", default="")
    start.add_argument("--alias", default="oracle")
    start.add_argument("--passcode")
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--http-port", type=int, default=9723)
    start.add_argument("--trusted-geda-aid", required=True)
    start.add_argument("--rpc-url", default="")
    start.add_argument("--no-post", action="store_true")
    start.add_argument("--incept-file")
    start.add_argument(
        "--association-schema",
        default="",
        help="Association schema JSON; requires --no-post",
    )
    start.add_argument(
        "--schema-dir", default="", help="Directory of source credential schemas"
    )
    start.set_defaults(handler=start_handler)


def start_handler(args) -> None:
    """Translate CLI flags into service configuration and run the HIO loop."""

    run_service(
        ServiceConfig(
            name=args.name,
            base=args.base,
            alias=args.alias,
            passcode=args.passcode,
            host=args.host,
            http_port=args.http_port,
            trusted_geda_aid=args.trusted_geda_aid,
            rpc_url=args.rpc_url,
            no_post=args.no_post,
            incept_file=args.incept_file,
            association_schema=args.association_schema,
            schema_dir=args.schema_dir,
        )
    )

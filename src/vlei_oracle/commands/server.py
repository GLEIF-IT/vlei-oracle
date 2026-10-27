"""`vlei-oracle server` command."""

from __future__ import annotations

import os

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
    start.add_argument("--chain-id", type=int, default=0)
    start.add_argument("--endorsement-registry", default="")
    start.add_argument("--identity-registry", default="")
    start.add_argument("--posting-key-env", default="VLEI_ORACLE_POSTING_KEY")
    start.add_argument("--confirmations", type=int, default=2)
    start.add_argument("--posting-interval", type=float, default=1.0)
    start.add_argument("--incept-file")
    start.add_argument(
        "--association-schema",
        default="",
        help="Association schema JSON; must match the pinned protocol",
    )
    start.add_argument(
        "--schema-dir", default="", help="Directory of source credential schemas"
    )
    start.set_defaults(handler=start_handler)

    receive = sub.add_parser("receive", help="Receive trusted verifier status; no Ethereum posting")
    receive.add_argument("--verifier-aid", required=True)
    receive.add_argument("--chain-id", required=True, type=int)
    receive.add_argument("--webhook-url", required=True, help="Exact public /v1/status-events URL")
    receive.add_argument("--verifier-kel", default="", help="Native CESR key-event stream for verifier bootstrap/rotation")
    receive.add_argument("--name", default="vlei-oracle-inbox")
    receive.add_argument("--base", default="")
    receive.add_argument("--alias", default="oracle")
    receive.add_argument("--passcode")
    receive.add_argument("--incept-file")
    receive.add_argument("--head-dir")
    receive.add_argument("--host", default="0.0.0.0")
    receive.add_argument("--http-port", type=int, default=9723)
    receive.set_defaults(handler=receive_handler)


def receive_handler(args):
    """Start only the durable webhook receiver with explicit peer and chain trust."""
    from vlei_oracle.receiving.service import ReceiverConfig, run_receiver

    run_receiver(ReceiverConfig(**{name: getattr(args, name) for name in (
        "verifier_aid", "chain_id", "webhook_url", "verifier_kel", "name", "base",
        "alias", "passcode", "incept_file", "head_dir", "host", "http_port",
    )}))


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
            chain_id=args.chain_id,
            endorsement_registry=args.endorsement_registry,
            identity_registry=args.identity_registry,
            posting_key=os.getenv(args.posting_key_env, "") if not args.no_post else "",
            confirmations=args.confirmations,
            posting_interval=args.posting_interval,
        )
    )

"""Oracle CLI argument groups and command dispatch."""

from __future__ import annotations

import argparse
import os
import sys

from keri import kering


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch a registered command, and normalize errors."""

    parser = argparse.ArgumentParser(prog="vlei-oracle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_parser(subparsers)
    args = parser.parse_args(argv)

    try:
        args.handler(args)
    except (
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        OSError,
        kering.KeriError,
    ) as exc:
        if os.getenv("DEBUG_VLEI_ORACLE"):
            raise
        print(f"vlei-oracle: {exc}", file=sys.stderr)
        return 1
    return 0


def add_parser(subparsers) -> None:
    """Register the always-on service command group."""

    parser = subparsers.add_parser(
        "server", help="Run the trusted-status oracle service"
    )
    sub = parser.add_subparsers(dest="server_command", required=True)
    receive = sub.add_parser(
        "receive", help="Receive trusted verifier status; no Ethereum posting"
    )
    _receiver_arguments(receive)
    receive.set_defaults(handler=receive_handler)
    start = sub.add_parser(
        "start", help="Receive trusted status and publish/withdraw on Ethereum"
    )
    _receiver_arguments(start)
    start.add_argument("--rpc-url", required=True)
    start.add_argument("--endorsement-registry", required=True)
    start.add_argument("--identity-registry", required=True)
    start.add_argument("--posting-key-env", default="VLEI_ORACLE_POSTING_KEY")
    start.add_argument("--confirmations", type=int, default=2)
    start.add_argument("--posting-interval", type=float, default=1.0)
    start.set_defaults(handler=receive_handler)


def _receiver_arguments(receive):
    """Share the identity and inbox options for receive-only and posting modes."""
    receive.add_argument("--verifier-aid", required=True)
    receive.add_argument("--chain-id", required=True, type=int)
    receive.add_argument(
        "--webhook-url", required=True, help="Exact public /v1/status-events URL"
    )
    receive.add_argument(
        "--verifier-kel",
        default="",
        help="CESR key-event stream for verifier bootstrap/rotation",
    )
    receive.add_argument("--name", default="vlei-oracle-inbox")
    receive.add_argument("--base", default="")
    receive.add_argument("--alias", default="oracle")
    receive.add_argument("--passcode")
    receive.add_argument("--incept-file")
    receive.add_argument("--head-dir")
    receive.add_argument("--host", default="0.0.0.0")
    receive.add_argument("--http-port", type=int, default=9723)


def receive_handler(args):
    """Compose trusted receipt with optional posting, without credential verification."""
    from vlei_oracle.runtime.config import ReceiverConfig
    from vlei_oracle.runtime.serving import run_receiver

    posting = {}
    if args.server_command == "start":
        posting = {
            name: getattr(args, name)
            for name in (
                "rpc_url",
                "endorsement_registry",
                "identity_registry",
                "confirmations",
                "posting_interval",
            )
        }
        posting["posting_key"] = os.getenv(args.posting_key_env, "")
    run_receiver(
        ReceiverConfig(
            **posting,
            **{
                name: getattr(args, name)
                for name in (
                    "verifier_aid",
                    "chain_id",
                    "webhook_url",
                    "verifier_kel",
                    "name",
                    "base",
                    "alias",
                    "passcode",
                    "incept_file",
                    "head_dir",
                    "host",
                    "http_port",
                )
            },
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

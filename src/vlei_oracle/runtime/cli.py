"""Oracle CLI argument groups and command dispatch."""

from __future__ import annotations

import argparse
import os
import sys

from keri import kering

from vlei_oracle.persistence.posting import PostingReader, PostingWriter


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
    start.add_argument("--max-attempts", type=int, default=3)
    start.add_argument("--pending-timeout", type=float, default=120.0)
    start.set_defaults(handler=receive_handler)
    retry = subparsers.add_parser(
        "retry", help="Retry one dead letter with the service stopped"
    )
    retry.add_argument("operation_id")
    retry.add_argument("--name", default="vlei-oracle-inbox")
    retry.add_argument("--base", default="")
    retry.add_argument("--head-dir")
    retry.set_defaults(handler=retry_handler)


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
                "max_attempts",
                "pending_timeout",
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


def retry_handler(args):
    """Move a corrected dead letter back to its phase using KERIpy LMDB ownership.

    Stop the service first: this is a local operator command, not a second writer.
    An unresolved signed transaction keeps its bytes and nonce; no new transaction is signed.
    """
    from keri.db import dbing

    from vlei_oracle.endorsement.retrying import RecoveryService
    from vlei_oracle.persistence.basing import OracleBaser

    with dbing.openLMDB(
        cls=OracleBaser,
        name=args.name,
        base=args.base,
        headDirPath=args.head_dir,
        temp=False,
    ) as baser:
        RecoveryService(PostingReader(baser), PostingWriter(baser)).retry_operation(
            args.operation_id
        )


if __name__ == "__main__":
    raise SystemExit(main())

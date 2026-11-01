"""Oracle CLI argument groups and command dispatch."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace

from keri import kering

from vlei_oracle.persistence.posting import PostingReader, PostingWriter

from .config import ReceiverConfig
from .serving import run_receiver


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch a registered command, and normalize errors."""

    args = make_parser().parse_args(argv)

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


def make_parser():
    """Declare receiver, posting and offline recovery commands without opening resources."""
    root = argparse.ArgumentParser(prog="vlei-oracle")
    commands = root.add_subparsers(dest="command", required=True)

    server_parser = commands.add_parser(
        "server", help="Run the trusted-status oracle service"
    )
    actions = server_parser.add_subparsers(dest="server_command", required=True)
    receive_parser = actions.add_parser(
        "receive", help="Receive trusted verifier status; no Ethereum posting"
    )
    _receiver_args(receive_parser)
    receive_parser.set_defaults(handler=receive_status)
    start_parser = actions.add_parser(
        "start", help="Receive trusted status and publish/withdraw on Ethereum"
    )
    _receiver_args(start_parser)
    start_parser.add_argument("--rpc-url", required=True)
    start_parser.add_argument("--endorsement-registry", required=True)
    start_parser.add_argument("--identity-registry", required=True)
    start_parser.add_argument("--posting-key-env", default="VLEI_ORACLE_POSTING_KEY")
    start_parser.add_argument(
        "--confirmations", type=int, default=ReceiverConfig.confirmations
    )
    start_parser.add_argument(
        "--posting-interval", type=float, default=ReceiverConfig.posting_interval
    )
    start_parser.add_argument(
        "--max-attempts", type=int, default=ReceiverConfig.max_attempts
    )
    start_parser.add_argument(
        "--pending-timeout", type=float, default=ReceiverConfig.pending_timeout
    )
    start_parser.set_defaults(handler=start_service)
    retry_parser = commands.add_parser(
        "retry", help="Retry one dead letter with the service stopped"
    )
    retry_parser.add_argument("operation_id")
    _store_args(retry_parser)
    retry_parser.set_defaults(handler=retry_operation)
    return root


def _receiver_args(parser):
    """Share the identity and inbox options for receive-only and posting modes."""
    parser.add_argument("--verifier-aid", required=True)
    parser.add_argument("--chain-id", required=True, type=int)
    parser.add_argument(
        "--webhook-url", required=True, help="Exact public /v1/status-events URL"
    )
    parser.add_argument(
        "--verifier-oobi",
        required=True,
        help="OOBI URL resolving the authorized Verifier AID",
    )
    _store_args(parser)
    parser.add_argument("--alias", default=ReceiverConfig.alias)
    parser.add_argument("--passcode")
    parser.add_argument("--incept-file")
    parser.add_argument("--host", default=ReceiverConfig.host)
    parser.add_argument("--http-port", type=int, default=ReceiverConfig.http_port)


def _store_args(parser):
    """Declare the persistent application-store selector used by both modes and retry."""
    parser.add_argument("--name", default=ReceiverConfig.name)
    parser.add_argument("--base", default=ReceiverConfig.base)
    parser.add_argument("--head-dir")


def _receiver_config(args):
    """Validate shared identity/trust/storage input without enabling Ethereum posting."""
    return ReceiverConfig(
        verifier_aid=args.verifier_aid,
        chain_id=args.chain_id,
        webhook_url=args.webhook_url,
        verifier_oobi=args.verifier_oobi,
        name=args.name,
        base=args.base,
        alias=args.alias,
        passcode=args.passcode,
        incept_file=args.incept_file,
        head_dir=args.head_dir,
        host=args.host,
        http_port=args.http_port,
    )


def receive_status(args):
    """Run authenticated status intake with no Ethereum writer or posting key lookup."""
    run_receiver(_receiver_config(args))


def start_service(args):
    """Add validated posting settings and its selected environment secret, then run the service."""
    config = replace(
        _receiver_config(args),
        rpc_url=args.rpc_url,
        endorsement_registry=args.endorsement_registry,
        identity_registry=args.identity_registry,
        posting_key=os.getenv(args.posting_key_env, ""),
        confirmations=args.confirmations,
        posting_interval=args.posting_interval,
        max_attempts=args.max_attempts,
        pending_timeout=args.pending_timeout,
    )
    run_receiver(config)


def retry_operation(args):
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

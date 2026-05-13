"""`vlei-oracle client` command."""

from __future__ import annotations

from vlei_oracle.clienting import build_signature_body, post_signature


def add_parser(subparsers) -> None:
    """Register client-side presentation helper commands."""

    parser = subparsers.add_parser("client", help="Run client-side presentation workflows")
    sub = parser.add_subparsers(dest="client_command", required=True)
    post_sig = sub.add_parser("post-signature", help="POST CESR-encoded Ethereum signature")
    post_sig.add_argument("--oracle-url", required=True)
    post_sig.add_argument("--wallet-said", required=True)
    post_sig.add_argument("--typed-data", required=True)
    post_sig.add_argument("--private-key")
    post_sig.add_argument("--cesr-signature")
    post_sig.add_argument("--wallet")
    post_sig.add_argument("--chain-id", type=int)
    post_sig.add_argument("--timeout", type=float, default=30.0)
    post_sig.set_defaults(handler=post_signature_handler)


def post_signature_handler(args) -> None:
    """Sign or forward a CESR signature and send it to a pending operation."""

    body = build_signature_body(
        typed_data_path=args.typed_data,
        private_key=args.private_key,
        cesr_signature=args.cesr_signature,
        wallet=args.wallet,
        chain_id=args.chain_id,
    )
    result = post_signature(
        oracle_url=args.oracle_url,
        wallet_said=args.wallet_said,
        body=body,
        timeout=args.timeout,
    )
    if result.body:
        print(result.body.decode("utf-8"))

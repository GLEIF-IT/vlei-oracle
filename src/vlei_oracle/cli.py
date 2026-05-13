"""Command line interface for the GL1 vLEI Oracle."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .contracts import load_json_file, parse_deploy_log, post_verified_binding
from .keri_store import presentation_from_keri_store
from .verifier import VerificationError, VerifiedWalletBinding, verify_presentation
from .wallet import build_typed_data, canonical_json, wallet_attributes_from_typed_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vlei-oracle")
    parser.add_argument("--cast-bin", default=None, help="Path to Foundry cast")
    sub = parser.add_subparsers(dest="command", required=True)

    challenge = sub.add_parser("challenge", help="Build GL1 wallet EIP-712 challenge typed data")
    _add_challenge_args(challenge)
    challenge.add_argument("--out", help="Write typed data JSON to this file")

    wallet_data = sub.add_parser("wallet-data", help="Build wallet attestation attributes from typed data")
    wallet_data.add_argument("--typed-data", required=True)
    wallet_data.add_argument("--signature", required=True)
    wallet_data.add_argument("--out", required=True)

    presentation = sub.add_parser("presentation-from-keri", help="Build verifier presentation from a KERIpy store")
    presentation.add_argument("--name", required=True)
    presentation.add_argument("--alias", required=True)
    presentation.add_argument("--said", required=True)
    presentation.add_argument("--out", required=True)
    presentation.add_argument("--base", default="")
    presentation.add_argument("--passcode")

    verify = sub.add_parser("verify-presentation", help="Verify GL1 wallet attestation presentation")
    verify.add_argument("--presentation", required=True)
    verify.add_argument("--trusted-geda-aid", required=True)
    verify.add_argument("--wallet-schema", required=True)
    verify.add_argument("--out", required=True)
    verify.add_argument("--skip-said-verification", action="store_true")

    post = sub.add_parser("post-verified", help="Post verified evidence to vlei-comply contracts")
    post.add_argument("--verified", required=True)
    post.add_argument("--contracts", required=True)
    post.add_argument("--rpc-url", required=True)
    post.add_argument("--private-key", required=True)
    post.add_argument("--initial-aid-allowed", action="store_true")

    parse = sub.add_parser("parse-deploy-log", help="Parse DeployAll stdout into contracts JSON")
    parse.add_argument("--log", required=True)
    parse.add_argument("--out", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "challenge":
            typed_data = _challenge_from_args(args)
            _write_or_print(typed_data, args.out)
        elif args.command == "wallet-data":
            typed_data = load_json_file(args.typed_data)
            attrs = wallet_attributes_from_typed_data(typed_data=typed_data, signature=args.signature)
            Path(args.out).write_text(json.dumps(attrs, indent=2, sort_keys=True) + "\n")
        elif args.command == "presentation-from-keri":
            presentation = presentation_from_keri_store(
                name=args.name,
                alias=args.alias,
                said=args.said,
                base=args.base,
                passcode=args.passcode,
            )
            Path(args.out).write_text(json.dumps(presentation, indent=2, sort_keys=True) + "\n")
        elif args.command == "verify-presentation":
            presentation = load_json_file(args.presentation)
            binding = verify_presentation(
                presentation,
                trusted_geda_aid=args.trusted_geda_aid,
                wallet_schema=args.wallet_schema,
                cast_bin=args.cast_bin,
                verify_saids=not args.skip_said_verification,
            )
            Path(args.out).write_text(json.dumps(binding.to_json(), indent=2, sort_keys=True) + "\n")
        elif args.command == "post-verified":
            data = load_json_file(args.verified)
            binding = VerifiedWalletBinding(
                legal_entity_aid=data["legalEntityAid"],
                lei=data["lei"],
                wallet=data["wallet"],
                chain_id=int(data["chainId"]),
                le_credential_said=data["leCredentialSaid"],
                oor_credential_said=data["oorCredentialSaid"],
                wallet_credential_said=data["walletCredentialSaid"],
                wallet_schema=data["walletSchema"],
                vlei_token=data["vleiToken"],
                wrapper=data["wrapper"],
                identity_registry=data["identityRegistry"],
                credential_registry=data["credentialRegistry"],
                oracle_registry=data["oracleRegistry"],
                nonce=data["nonce"],
                issued_at=int(data.get("issuedAt") or 0),
                expires_at=int(data["expiresAt"]),
                purpose=data["purpose"],
            )
            contracts = load_json_file(args.contracts)
            post_verified_binding(
                binding,
                contracts=contracts,
                rpc_url=args.rpc_url,
                private_key=args.private_key,
                cast_bin=args.cast_bin,
                initial_aid_allowed=args.initial_aid_allowed,
            )
        elif args.command == "parse-deploy-log":
            parsed = parse_deploy_log(Path(args.log).read_text())
            Path(args.out).write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n")
    except (VerificationError, ValueError, FileNotFoundError) as exc:
        print(f"vlei-oracle: {exc}", file=sys.stderr)
        return 1

    return 0


def _add_challenge_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--legal-entity-aid", required=True)
    parser.add_argument("--lei", required=True)
    parser.add_argument("--oor-aid", required=True)
    parser.add_argument("--le-credential-said", required=True)
    parser.add_argument("--oor-credential-said", required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--vlei-token", required=True)
    parser.add_argument("--wrapper", required=True)
    parser.add_argument("--identity-registry", required=True)
    parser.add_argument("--credential-registry", required=True)
    parser.add_argument("--oracle-registry", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--issued-at", type=int, default=0)
    parser.add_argument("--expires-at", type=int, default=0)
    parser.add_argument("--purpose", default="GL1_WVLEI_TRANSFER")


def _challenge_from_args(args: argparse.Namespace) -> dict:
    issued_at = args.issued_at or int(time.time())
    expires_at = args.expires_at or issued_at + 3600
    return build_typed_data(
        legal_entity_aid=args.legal_entity_aid,
        lei=args.lei,
        oor_aid=args.oor_aid,
        le_credential_said=args.le_credential_said,
        oor_credential_said=args.oor_credential_said,
        wallet=args.wallet,
        chain_id=args.chain_id,
        vlei_token=args.vlei_token,
        wrapper=args.wrapper,
        identity_registry=args.identity_registry,
        credential_registry=args.credential_registry,
        oracle_registry=args.oracle_registry,
        nonce=args.nonce,
        expires_at=expires_at,
        purpose=args.purpose,
        issued_at=issued_at,
    )


def _write_or_print(data: dict, out: str | None) -> None:
    raw = canonical_json(data) + "\n"
    if out:
        Path(out).write_text(raw)
    else:
        print(raw, end="")


if __name__ == "__main__":
    raise SystemExit(main())

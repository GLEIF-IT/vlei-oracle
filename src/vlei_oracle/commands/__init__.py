"""CLI command registration for vlei-oracle."""

from __future__ import annotations

from . import challenge, client, parse_deploy_log, server, wallet_data


def load_commands(subparsers) -> None:
    """Register all top-level vlei-oracle commands."""

    for module in (server, client, challenge, wallet_data, parse_deploy_log):
        module.add_parser(subparsers)

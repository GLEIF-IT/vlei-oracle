"""CLI command registration for vlei-oracle."""

from __future__ import annotations

from . import parse_deploy_log, server


def load_commands(subparsers) -> None:
    """Register all top-level vlei-oracle commands."""

    for module in (server, parse_deploy_log):
        module.add_parser(subparsers)

"""Root command line entrypoint for vlei-oracle.

Command implementations live under ``vlei_oracle.commands`` so the CLI stays
easy to scan as service and deployment helpers grow.
"""

from __future__ import annotations

import argparse
import os
import sys

from keri import kering

from .commands import load_commands


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch a registered command, and normalize errors."""

    parser = argparse.ArgumentParser(prog="vlei-oracle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    load_commands(subparsers)
    args = parser.parse_args(argv)

    try:
        args.handler(args)
    except (ValueError, KeyError, TypeError, RuntimeError, OSError, kering.KeriError) as exc:
        if os.getenv("DEBUG_VLEI_ORACLE"):
            raise
        print(f"vlei-oracle: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

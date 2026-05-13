"""Root command line entrypoint for vlei-oracle."""

from __future__ import annotations

import argparse
import os
import sys

from .commands import load_commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vlei-oracle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    load_commands(subparsers)
    args = parser.parse_args(argv)

    try:
        args.handler(args)
    except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
        if os.getenv("DEBUG_VLEI_ORACLE"):
            raise
        print(f"vlei-oracle: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

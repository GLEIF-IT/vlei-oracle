"""Public CLI for Oracle's local USDC integration environment.

Run with ``python -m scripts.integration.env`` from the selected tooling root.
Applications execute from fresh wheels. Preparation/lifetimes belong to
``environment`` and USDC assertions remain in the sole maintained pytest scenario.
"""

import argparse
import json
import os
from pathlib import Path
import sys

from .environment import (
    call_verifier,
    clean_run,
    initialize_identity,
    load_run,
    prepare_run,
    run_flow,
    start_run,
    stop_run,
)
from .processes import is_running, process_records

SOURCE = Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    """Describe explicit run ownership and optional candidate-source selection."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=(
            "prepare",
            "start",
            "flow",
            "qualify",
            "status",
            "logs",
            "stop",
            "clean",
            "_initialize",
        ),
    )
    result.add_argument("--run-dir", required=True, type=Path)
    result.add_argument("--verifier-source", type=Path)
    result.add_argument("--comply-source", type=Path)
    result.add_argument("--oracle-source", type=Path, default=SOURCE)
    result.add_argument("--oracle-wheel", type=Path)
    result.add_argument("--tool-revision")
    result.add_argument("--witness-count", type=int, choices=(1, 6), default=6)
    result.add_argument("--service")
    return result


def main() -> None:
    """Select the exact run, enter its interpreter and execute one concrete command."""
    arguments = parser().parse_args()
    root = arguments.run_dir.expanduser().resolve()
    if arguments.command in ("prepare", "qualify") and not root.exists():
        prepare_run(
            root,
            arguments.oracle_source.resolve(),
            verifier_source=arguments.verifier_source,
            comply_source=arguments.comply_source,
            witness_count=arguments.witness_count,
            wheel=arguments.oracle_wheel,
            tool_revision=arguments.tool_revision,
        )
        if arguments.command == "prepare":
            print(f"Prepared {root}")
            return
    elif arguments.command == "prepare":
        raise ValueError("Run already exists; choose a fresh directory")
    configuration = load_run(root)
    if arguments.command in ("status", "logs", "stop", "clean"):
        inspect_or_stop(root, configuration, arguments)
        return
    interpreter_root = str(Path(configuration["python"]).parents[1])
    if sys.prefix != interpreter_root:
        os.chdir(Path(configuration["tool"]).parents[2])
        os.execv(
            configuration["python"],
            [configuration["python"], "-m", "scripts.integration.env", *sys.argv[1:]],
        )
    if arguments.command == "_initialize":
        initialize_identity(root, configuration)
    elif arguments.command == "start":
        start_run(root, configuration)
    elif arguments.command == "flow":
        run_flow(root, configuration)
    elif arguments.command == "qualify":
        try:
            start_run(root, configuration)
            run_flow(root, configuration)
        finally:
            stop_run(root)


def inspect_or_stop(root: Path, configuration: dict, arguments) -> None:
    """Operate on owned process records without importing either application."""
    if arguments.command == "stop":
        stop_run(root)
    elif arguments.command == "clean":
        clean_run(root)
    elif arguments.command == "status":
        print(
            json.dumps(
                {
                    "run": configuration,
                    "processes": {
                        name: is_running(record)
                        for name, record in process_records(root).items()
                    },
                },
                indent=2,
            )
        )
        call_verifier(root, "status", visible=True)
    else:
        for path in sorted((root / "logs").glob((arguments.service or "*") + ".log")):
            print(f"{path}\n{path.read_text(errors='replace')[-12000:]}")
        call_verifier(root, "logs", visible=True)


if __name__ == "__main__":
    main()

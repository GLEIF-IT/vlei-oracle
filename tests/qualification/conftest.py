"""Own or attach the maintained installed-wheel paired qualification environment."""

import os
from pathlib import Path

import pytest

from scripts.integration.artifacts import source_revision


@pytest.fixture
def integration_run(tmp_path):
    """Attach an explicit started run, or own a fresh installed-wheel stack.

    The external run is managed by the operator command. A pytest-created run
    closes all child processes even when qualification fails, retaining evidence.
    """
    from scripts.integration.artifacts import load_run
    from scripts.integration.environment import (
        start_run,
        stop_run,
    )
    from scripts.integration.preparing import prepare_run

    selected = os.getenv("ORACLE_INTEGRATION_RUN")
    if selected:
        root = Path(selected).resolve()
        load_run(root)
        yield root
        return
    wheel = os.environ.get("ORACLE_WHEEL")
    assert wheel and Path(wheel).is_file(), (
        "Set ORACLE_WHEEL to the fresh qualified wheel"
    )
    root = (tmp_path / "stack").resolve()
    source = Path(__file__).resolve().parents[2]
    prepare_run(
        root,
        source,
        verifier_source=Path(os.environ["VERIFIER_TOOL_SOURCE"])
        if os.getenv("VERIFIER_TOOL_SOURCE")
        else None,
        comply_source=Path(os.environ["VLEI_COMPLY_DIR"])
        if os.getenv("VLEI_COMPLY_DIR")
        else None,
        witness_count=1,
        wheel=Path(wheel),
        tool_revision=os.getenv("GITHUB_SHA") or source_revision(source),
    )
    try:
        start_run(root, load_run(root))
        yield root
    finally:
        stop_run(root)

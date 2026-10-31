"""runtime/test_demo_checkout.py."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location(
    "prepare_demo", ROOT / "scripts/dev/prepare-demo.py"
)

prepare_demo = importlib.util.module_from_spec(spec)

spec.loader.exec_module(prepare_demo)


def git(directory: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(directory), *arguments], text=True
    ).strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[str, str]:
    source = tmp_path / "upstream"
    source.mkdir()
    git(source, "init", "--quiet")
    git(source, "config", "user.email", "fixture@example.invalid")
    git(source, "config", "user.name", "Disposable checkout fixture")
    (source / "asset.txt").write_text("first revision\n")
    git(source, "add", "asset.txt")
    git(source, "commit", "--quiet", "-m", "Create disposable fixture")
    return source.as_uri(), git(source, "rev-parse", "HEAD")


def test_pinned_checkout_relocates_and_reuses_exact_clean_revision(
    tmp_path: Path, upstream: tuple[str, str]
) -> None:
    url, revision = upstream
    checkout = tmp_path / "a directory with spaces" / "dependency"
    prepare_demo.prepare_checkout(checkout, url=url, commit=revision)
    prepare_demo.prepare_checkout(checkout, url=url, commit=revision)
    assert git(checkout, "rev-parse", "HEAD") == revision
    assert (checkout / "asset.txt").read_text() == "first revision\n"


@pytest.mark.parametrize("mismatch", ["dirty", "revision", "origin", "not_a_checkout"])
def test_existing_dependency_is_never_reset(
    tmp_path: Path, upstream: tuple[str, str], mismatch: str
) -> None:
    url, revision = upstream
    checkout = tmp_path / "dependency"
    prepare_demo.prepare_checkout(checkout, url=url, commit=revision)
    expected_revision = revision
    expected_url = url
    if mismatch == "dirty":
        (checkout / "asset.txt").write_text("preserve this work\n")
    elif mismatch == "revision":
        expected_revision = "0" * 40
    elif mismatch == "origin":
        expected_url = "https://example.invalid/other.git"
    else:
        checkout = tmp_path / "ordinary_directory"
        checkout.mkdir()
        (checkout / "asset.txt").write_text("preserve this directory\n")
    before = (checkout / "asset.txt").read_bytes()
    with pytest.raises(ValueError):
        prepare_demo.prepare_checkout(
            checkout, url=expected_url, commit=expected_revision
        )
    assert (checkout / "asset.txt").read_bytes() == before


def test_environment_roundtrips_paths_containing_shell_metacharacters(
    tmp_path: Path,
) -> None:
    path = tmp_path / "demo.env"
    selected_path = str(tmp_path / "a space's $literal; directory")
    prepare_demo.write_environment(path, {"ORACLE_DIR": selected_path})
    actual = subprocess.check_output(
        ["bash", "-c", 'source "$1"; printf "%s" "$ORACLE_DIR"', "_", str(path)],
        text=True,
    )
    assert actual == selected_path


def test_prepared_python_environment_executes_interpreter_and_pip(
    tmp_path: Path,
) -> None:
    python = prepare_demo.prepare_python_environment(
        tmp_path / "environment with spaces"
    )
    subprocess.run(
        [str(python), "-c", "import sys; assert sys.prefix != sys.base_prefix"],
        check=True,
    )
    subprocess.run(
        [str(python), "-m", "pip", "--version"], check=True, capture_output=True
    )


def test_environment_creation_through_symlinked_python_launcher(tmp_path: Path) -> None:
    import sys

    launcher = tmp_path / "python-launcher"
    launcher.symlink_to(sys.executable)
    environment = tmp_path / "new environment"
    subprocess.run(
        [
            str(launcher),
            "-c",
            (
                "import runpy, sys; from pathlib import Path; "
                "runpy.run_path(sys.argv[1])['prepare_python_environment'](Path(sys.argv[2]))"
            ),
            str(ROOT / "scripts/dev/prepare-demo.py"),
            str(environment),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(environment / "bin/python"),
            "-c",
            "import encodings, sys; assert sys.prefix != sys.base_prefix",
        ],
        check=True,
    )


def test_oracle_build_inputs_exclude_removed_modules_and_cached_metadata(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "build"
    package = source / "src/vlei_oracle"
    package.mkdir(parents=True)
    destination.mkdir()
    for filename in ("pyproject.toml", "README.md"):
        (source / filename).write_text("current inputs")
    (package / "current.py").write_text("current = True")
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "old.pyc").write_bytes(b"stale")
    metadata = source / "src/vlei_oracle.egg-info"
    metadata.mkdir()
    (metadata / "SOURCES.txt").write_text("old.py")
    stale_build = source / "build/lib/vlei_oracle"
    stale_build.mkdir(parents=True)
    (stale_build / "old.py").write_text("obsolete = True")
    prepare_demo.copy_oracle_source(source, destination)
    assert sorted(
        str(file.relative_to(destination))
        for file in destination.rglob("*")
        if file.is_file()
    ) == [
        "README.md",
        "pyproject.toml",
        "src/vlei_oracle/current.py",
    ]
    assert (stale_build / "old.py").is_file()

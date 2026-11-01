"""Exact dependency checkout selection must not modify existing work."""

import subprocess
from pathlib import Path

import pytest

from scripts.integration.checkouts import fetch_checkout


def git(directory: Path, *arguments: str) -> str:
    """Run Git directly so paths remain literal arguments."""
    return subprocess.check_output(
        ["git", "-C", str(directory), *arguments], text=True
    ).strip()


@pytest.fixture
def upstream(tmp_path):
    """Own a disposable local repository with one exact revision."""
    source = tmp_path / "upstream"
    source.mkdir()
    git(source, "init", "--quiet")
    git(source, "config", "user.email", "fixture@example.invalid")
    git(source, "config", "user.name", "Disposable checkout fixture")
    (source / "asset.txt").write_text("first revision\n")
    git(source, "add", "asset.txt")
    git(source, "commit", "--quiet", "-m", "Create disposable fixture")
    return source.as_uri(), git(source, "rev-parse", "HEAD")


def test_exact_checkout_supports_literal_paths(tmp_path, upstream):
    url, revision = upstream
    checkout = tmp_path / "a space's $literal; directory" / "dependency"
    fetch_checkout(checkout, url, revision)
    assert git(checkout, "rev-parse", "HEAD") == revision
    assert (checkout / "asset.txt").read_text() == "first revision\n"


@pytest.mark.parametrize(
    "mismatch", ["clean", "dirty", "revision", "origin", "not_a_checkout"]
)
def test_existing_dependency_is_never_reset(tmp_path, upstream, mismatch):
    url, revision = upstream
    checkout = tmp_path / "dependency"
    fetch_checkout(checkout, url, revision)
    if mismatch == "dirty":
        (checkout / "asset.txt").write_text("preserve this work\n")
    elif mismatch == "revision":
        revision = "0" * 40
    elif mismatch == "origin":
        url = "https://example.invalid/other.git"
    elif mismatch == "not_a_checkout":
        checkout = tmp_path / "ordinary_directory"
        checkout.mkdir()
        (checkout / "asset.txt").write_text("preserve this directory\n")
    before = (checkout / "asset.txt").read_bytes()
    with pytest.raises(ValueError, match="existing checkout"):
        fetch_checkout(checkout, url, revision)
    assert (checkout / "asset.txt").read_bytes() == before

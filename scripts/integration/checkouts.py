"""Select exact, fresh integration-tool and contract checkouts without resets."""

from pathlib import Path
import subprocess


def fetch_checkout(directory: Path, url: str, revision: str) -> None:
    """Create a detached checkout at revision and initialize its recorded gitlinks.

    Existing directories are refused. GitHub authentication uses the caller's gh
    credential helper for this command only; tokens never enter run artifacts.
    """
    if directory.exists():
        raise ValueError(f"Refusing existing checkout {directory}")
    subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
    git = ["git", "-C", str(directory)]
    subprocess.run([*git, "remote", "add", "origin", url], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "credential.helper=",
            "-c",
            "credential.helper=!gh auth git-credential",
            "fetch",
            "--depth=1",
            "origin",
            revision,
        ],
        check=True,
    )
    subprocess.run([*git, "checkout", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    if actual != revision:
        raise ValueError("Dependency did not resolve to its exact requested commit")
    subprocess.run([*git, "submodule", "update", "--init", "--recursive"], check=True)

"""Fail when the last commit that touched signers.yaml is unsigned."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )
    return (result.stdout or "").strip()


def last_signers_commit(repo: Path) -> str:
    return _git(repo, "log", "--format=%H", "-1", "--", "signers.yaml")


def is_signed(repo: Path, commit: str) -> bool:
    # Read the signature header itself: %G? reports N for an SSH signature when no
    # allowed-signers file is configured, which is every CI runner.
    header = _git(repo, "cat-file", "commit", commit).split("\n\n", 1)[0]
    return any(line.startswith(("gpgsig ", "gpgsig-sha256 ")) for line in header.splitlines())


def check_signers_commit(repo: Path | None = None) -> int:
    root = repo or Path.cwd()
    sha = last_signers_commit(root)
    if not sha:
        print("No commits found for signers.yaml; skipping signature check")
        return 0
    if not is_signed(root, sha):
        print(f"Last commit to signers.yaml is unsigned: {sha}", file=sys.stderr)
        return 1
    print(f"signers.yaml commit {sha} is signed")
    return 0


def main() -> int:
    return check_signers_commit(Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())

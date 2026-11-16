from __future__ import annotations

import os
import subprocess
from pathlib import Path

from bakeoff_results.signers_commit import check_signers_commit

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=_GIT_ENV, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "signers.yaml").write_text("schema: bakeoff-results-signers/v1\n")
    _git(repo, "add", "signers.yaml")
    return repo


def test_unsigned_signers_commit_fails_even_if_message_mentions_ssh(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "commit", "-m", "gpgsig ssh allowlist")
    assert check_signers_commit(repo) == 1


def test_signed_signers_commit_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    key = tmp_path / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-q"],
        check=True,
        capture_output=True,
    )
    _git(repo, "config", "gpg.format", "ssh")
    _git(repo, "config", "user.signingkey", str(key))
    _git(repo, "commit", "-S", "-m", "signed allowlist")
    assert check_signers_commit(repo) == 0

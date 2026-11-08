"""Queue store path confinement for runner, job, and submission ids."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bakeoff_results.queue_store import (
    QueueStoreError,
    _under_root,
    enqueue,
    get_runner,
    heartbeat,
    upsert_runner,
    write_submission,
)


def _root() -> Path:
    return Path(tempfile.mkdtemp())


@pytest.mark.parametrize(
    "runner_id",
    ["../evil", "foo/../bar", "foo/bar", "/etc/passwd", "..", ".", "a" * 129],
)
def test_upsert_runner_rejects_path_injection(runner_id: str) -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="invalid runner_id"):
        upsert_runner(root, {"runner_id": runner_id})
    assert not (root.parent / "evil.json").exists()
    runners = root / "runners"
    if runners.exists():
        assert list(runners.glob("*")) == []


@pytest.mark.parametrize("runner_id", ["../evil", "foo/bar", "/etc/passwd"])
def test_get_runner_rejects_path_injection(runner_id: str) -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="invalid runner_id"):
        get_runner(root, runner_id)


def test_under_root_rejects_sibling_prefix_and_root() -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="path escapes data directory"):
        _under_root(root, Path(f"{root}-evil") / "runner-1.json")
    with pytest.raises(QueueStoreError, match="path escapes data directory"):
        _under_root(root, root)
    assert (
        _under_root(root, root / "runners" / "runner-1.json") == root / "runners" / "runner-1.json"
    )


def test_upsert_runner_accepts_safe_id() -> None:
    root = _root()
    out = upsert_runner(root, {"runner_id": "runner-1"})
    assert out["runner_id"] == "runner-1"
    assert (root / "runners" / "runner-1.json").is_file()


@pytest.mark.parametrize("queue_id", ["../evil", "foo/bar", "/tmp/x"])
def test_enqueue_rejects_path_injection(queue_id: str) -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="invalid queue_id"):
        enqueue(root, {"model_id": "m", "queue_id": queue_id})
    pending = root / "run_queue" / "pending"
    if pending.exists():
        assert list(pending.glob("*")) == []


def test_heartbeat_rejects_path_injection() -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="invalid queue_id"):
        heartbeat(root, "../evil", "runner-1")


@pytest.mark.parametrize(
    ("runner_id", "run_id"),
    [("../evil", "run-1"), ("runner-1", "../evil"), ("a/b", "c"), ("r", "/abs")],
)
def test_write_submission_rejects_path_injection(runner_id: str, run_id: str) -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="invalid "):
        write_submission(root, runner_id, run_id, {"result": {}})
    submissions = root / "submissions"
    if submissions.exists():
        for path in submissions.rglob("*"):
            assert path.is_relative_to(root)

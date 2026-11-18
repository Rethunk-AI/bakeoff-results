"""Queue store path confinement for runner, job, and submission ids."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from bakeoff_results.queue_signing import generate_keypair
from bakeoff_results.queue_store import (
    QueueStoreError,
    _under_root,
    claim,
    complete,
    enqueue,
    fail,
    get_runner,
    heartbeat,
    list_completed,
    list_pending,
    load_whitelist,
    reap_stale_claims,
    requeue,
    set_runner_status,
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


def test_claim_breaks_a_created_at_tie_by_queue_id() -> None:
    root = _root()
    stamp = "2026-11-16T00:00:00Z"
    for queue_id in ("t-09", "t-02", "t-17"):
        enqueue(root, {"model_id": "m", "queue_id": queue_id, "created_at": stamp})

    claimed = []
    for _ in range(3):
        job = claim(root, "runner-1")
        assert job is not None
        claimed.append(job["queue_id"])

    # Directory order follows creation order, so a tie that ignores the queue_id
    # would hand out t-09 first and make claim order filesystem-dependent.
    assert claimed == ["t-02", "t-09", "t-17"]


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


def test_requeue_returns_claimed_and_completed_jobs_to_pending() -> None:
    root = _root()
    upsert_runner(root, {"runner_id": "runner-1"})
    job = enqueue(root, {"model_id": "m", "queue_id": "job-1", "run_id": "run-1"})
    claimed = claim(root, "runner-1")
    assert claimed is not None
    runner = get_runner(root, "runner-1")
    assert runner is not None
    assert runner["current_claim"] == "job-1"

    pending = requeue(root, "run-1")
    assert pending["status"] == "PENDING"
    assert pending["claimed_by"] is None
    runner = get_runner(root, "runner-1")
    assert runner is not None
    assert runner["current_claim"] is None

    claimed = claim(root, "runner-1")
    assert claimed is not None
    complete(root, job["queue_id"], "runner-1")
    assert list_completed(root)[0]["queue_id"] == "job-1"

    again = requeue(root, "run-1")
    assert again["status"] == "PENDING"
    assert again["completed_at"] is None
    assert [item["queue_id"] for item in list_pending(root)] == ["job-1"]
    assert list_completed(root) == []

    with pytest.raises(QueueStoreError, match="unknown job"):
        requeue(root, "missing")


def test_write_submission_refuses_an_unusable_or_missing_runner_key() -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="no result object"):
        write_submission(root, "runner-1", "run-1", {})
    with pytest.raises(QueueStoreError, match="unknown runner"):
        write_submission(root, "runner-1", "run-1", {"result": {"run_id": "run-1"}})

    upsert_runner(root, {"runner_id": "runner-1"})
    with pytest.raises(QueueStoreError, match="no registered public key"):
        write_submission(root, "runner-1", "run-1", {"result": {"run_id": "run-1"}})

    upsert_runner(root, {"runner_id": "runner-1", "public_key": "AAAA"})
    with pytest.raises(QueueStoreError, match="unusable public key"):
        write_submission(root, "runner-1", "run-1", {"result": {"run_id": "run-1"}})
    _, public_key = generate_keypair()
    upsert_runner(root, {"runner_id": "runner-1", "public_key": public_key})
    bundle = write_submission(root, "runner-1", "run-1", {"result": {"run_id": "run-1"}})
    assert (bundle / "result.json").is_file()


def test_claim_skips_future_retry_and_reap_marks_a_stale_runner_dead() -> None:
    root = _root()
    upsert_runner(root, {"runner_id": "runner-1"})
    enqueue(
        root,
        {
            "model_id": "later",
            "queue_id": "later",
            "retry_after": "2099-01-01T00:00:00Z",
        },
    )
    enqueue(root, {"model_id": "now", "queue_id": "now", "quantization": "q8"})
    assert claim(root, "runner-1", {"quantization": {"q8": True}}) is None
    now = claim(root, "runner-1")
    assert now is not None
    assert now["queue_id"] == "now"

    job = list_pending(root)
    stale = next(item for item in job if item["queue_id"] == "now")
    stale["claimed_at"] = "2000-01-01T00:00:00Z"
    stale["last_heartbeat"] = "2000-01-01T00:00:00Z"
    (root / "run_queue" / "pending" / "now.json").write_text(
        json.dumps(stale, indent=2, sort_keys=True) + "\n"
    )
    (root / "run_queue" / "pending" / "broken.json").write_text("[]\n")
    (root / "run_queue" / "pending" / "no-stamp.json").write_text(
        json.dumps({"status": "CLAIMED", "queue_id": "no-stamp", "claimed_by": "x"}) + "\n"
    )
    (root / "run_queue" / "pending" / "bad-time.json").write_text(
        json.dumps(
            {
                "status": "CLAIMED",
                "queue_id": "bad-time",
                "claimed_by": "x",
                "last_heartbeat": "not-a-date",
            }
        )
        + "\n"
    )
    assert reap_stale_claims(root, timeout_seconds=60) == ["now"]
    stale_runner = get_runner(root, "runner-1")
    assert stale_runner is not None
    assert stale_runner["status"] == "DEAD"
    assert stale_runner["current_claim"] is None


def test_heartbeat_and_complete_belong_only_to_the_claiming_runner() -> None:
    root = _root()
    upsert_runner(root, {"runner_id": "runner-1"})
    enqueue(root, {"model_id": "m", "queue_id": "job-1"})
    with pytest.raises(QueueStoreError, match="unknown in-flight job"):
        heartbeat(root, "missing", "runner-1")
    with pytest.raises(QueueStoreError, match="unknown in-flight job"):
        complete(root, "missing", "runner-1")
    with pytest.raises(QueueStoreError, match="unknown in-flight job"):
        fail(root, "missing", "boom", "runner-1")

    claimed = claim(root, "runner-1")
    assert claimed is not None
    with pytest.raises(QueueStoreError, match="not claimed by this runner"):
        heartbeat(root, "job-1", "other")
    with pytest.raises(QueueStoreError, match="not claimed by this runner"):
        complete(root, "job-1", "other")
    with pytest.raises(QueueStoreError, match="not claimed by this runner"):
        fail(root, "job-1", "boom", "other")

    pending_path = root / "run_queue" / "pending" / "job-1.json"
    parked = json.loads(pending_path.read_text())
    parked["status"] = "PENDING"
    pending_path.write_text(json.dumps(parked, indent=2, sort_keys=True) + "\n")
    with pytest.raises(QueueStoreError, match="not in-flight"):
        heartbeat(root, "job-1", "runner-1")


def test_status_and_enqueue_and_whitelist_reject_invalid_records() -> None:
    root = _root()
    with pytest.raises(QueueStoreError, match="queue item requires model_id"):
        enqueue(root, {"queue_id": "job-1"})
    with pytest.raises(QueueStoreError, match="invalid runner status"):
        set_runner_status(root, "runner-1", "ONLINE")
    with pytest.raises(QueueStoreError, match="unknown runner"):
        set_runner_status(root, "runner-1", "DEAD")

    upsert_runner(root, {"runner_id": "runner-1", "current_claim": "job-1"})
    dead = set_runner_status(root, "runner-1", "DEAD")
    assert dead["current_claim"] is None

    (root / "runners" / "runner-1.json").write_text("[]\n")
    with pytest.raises(QueueStoreError, match="JSON object"):
        get_runner(root, "runner-1")

    (root / "whitelist.json").write_text(json.dumps({"public_keys": "not-a-list"}) + "\n")
    with pytest.raises(QueueStoreError, match="public_keys"):
        load_whitelist(root)

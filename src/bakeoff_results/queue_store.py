"""Disk-backed runner roster and job queue for distributed worker mode.

Layout under BAKEOFF_RESULTS_DATA_DIR (default ~/.local/share/bakeoff-results)::

    runners/<runner_id>.json
    run_queue/pending/<queue_id>.json
    run_queue/completed/<queue_id>.json
    submissions/<runner_id>/<run_id>/result.json
    whitelist.json

Claim uses the same rename-as-mutex protocol as Rethunk-AI/bakeoff ``bench.queue``:
``os.rename(pending/<id>.json → pending/<id>.lck-<runner_id>)`` is the gate, so
concurrent claimants cannot both win. There is no SQL runtime here; SKIP LOCKED
is the rename.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

_PENDING = "pending"
_COMPLETED = "completed"
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"

STATUS_PENDING = "PENDING"
STATUS_CLAIMED = "CLAIMED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

RUNNER_ACTIVE = "ACTIVE"
RUNNER_IDLE = "IDLE"
RUNNER_DEAD = "DEAD"

_DEFAULT_DATA_DIR = "~/.local/share/bakeoff-results"
_RETRY_BACKOFF_MINUTES = 5
_RETRY_PRIORITY_BUMP = 5


class QueueStoreError(ValueError):
    """Raised when a queue or roster operation cannot be completed."""


def data_dir() -> Path:
    raw = os.environ.get("BAKEOFF_RESULTS_DATA_DIR", _DEFAULT_DATA_DIR)
    return Path(os.path.expanduser(raw)).resolve()


def utc_now() -> str:
    return datetime.now(UTC).strftime(_ISO_FMT)


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, _ISO_FMT).replace(tzinfo=UTC)


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        data: dict[str, Any] = json.load(handle)
    if not isinstance(data, dict):
        raise QueueStoreError(f"{path} must contain a JSON object")
    return data


def _queue_dir(root: Path) -> Path:
    return root / "run_queue"


def pending_dir(root: Path) -> Path:
    path = _queue_dir(root) / _PENDING
    path.mkdir(parents=True, exist_ok=True)
    return path


def completed_dir(root: Path) -> Path:
    path = _queue_dir(root) / _COMPLETED
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_path(root: Path, queue_id: str) -> Path:
    return pending_dir(root) / f"{queue_id}.json"


def _completed_path(root: Path, queue_id: str) -> Path:
    return completed_dir(root) / f"{queue_id}.json"


def _lock_path(root: Path, queue_id: str, runner_id: str) -> Path:
    return pending_dir(root) / f"{queue_id}.lck-{runner_id}"


def _runners_dir(root: Path) -> Path:
    path = root / "runners"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runner_path(root: Path, runner_id: str) -> Path:
    return _runners_dir(root) / f"{runner_id}.json"


def _whitelist_path(root: Path) -> Path:
    return root / "whitelist.json"


def load_whitelist(root: Path) -> list[str]:
    path = _whitelist_path(root)
    if not path.is_file():
        return []
    data = _read_json(path)
    keys = data.get("public_keys", [])
    if not isinstance(keys, list) or not all(isinstance(item, str) and item for item in keys):
        raise QueueStoreError("whitelist.json public_keys must be a list of strings")
    return list(keys)


def save_whitelist(root: Path, public_keys: list[str]) -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for key in public_keys:
        if key not in seen:
            unique.append(key)
            seen.add(key)
    _atomic_write(_whitelist_path(root), {"public_keys": unique})


def add_whitelist_key(root: Path, public_key: str) -> list[str]:
    keys = load_whitelist(root)
    if public_key not in keys:
        keys.append(public_key)
        save_whitelist(root, keys)
    return keys


def remove_whitelist_key(root: Path, public_key: str) -> list[str]:
    keys = [key for key in load_whitelist(root) if key != public_key]
    save_whitelist(root, keys)
    return keys


def upsert_runner(root: Path, runner: dict[str, Any]) -> dict[str, Any]:
    runner_id = str(runner["runner_id"])
    path = _runner_path(root, runner_id)
    existing: dict[str, Any] | None = None
    if path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError, QueueStoreError):
            existing = _read_json(path)
    now = utc_now()
    out = dict(existing or {})
    out.update(runner)
    out["runner_id"] = runner_id
    out.setdefault("status", RUNNER_IDLE)
    out.setdefault("registered_at", now)
    out["updated_at"] = now
    _atomic_write(path, out)
    return out


def get_runner(root: Path, runner_id: str) -> dict[str, Any] | None:
    path = _runner_path(root, runner_id)
    if not path.is_file():
        return None
    return _read_json(path)


def list_runners(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _runners_dir(root).glob("*.json"):
        with contextlib.suppress(OSError, json.JSONDecodeError, QueueStoreError):
            items.append(_read_json(path))
    items.sort(key=lambda item: str(item.get("runner_id", "")))
    return items


def set_runner_status(root: Path, runner_id: str, status: str) -> dict[str, Any]:
    if status not in {RUNNER_ACTIVE, RUNNER_IDLE, RUNNER_DEAD}:
        raise QueueStoreError(f"invalid runner status: {status}")
    runner = get_runner(root, runner_id)
    if runner is None:
        raise QueueStoreError(f"unknown runner: {runner_id}")
    runner["status"] = status
    runner["updated_at"] = utc_now()
    if status != RUNNER_ACTIVE:
        runner["current_claim"] = None
    _atomic_write(_runner_path(root, runner_id), runner)
    return runner


def _stamp(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    now = utc_now()
    out.setdefault("created_at", now)
    out["updated_at"] = now
    return out


def enqueue(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out.setdefault("queue_id", str(uuid4()))
    out.setdefault("run_id", out["queue_id"])
    if not out.get("model_id"):
        raise QueueStoreError("queue item requires model_id")
    out.setdefault("priority", 100)
    out.setdefault("status", STATUS_PENDING)
    out.setdefault("attempt_count", 0)
    out.setdefault("max_attempts", 5)
    out = _stamp(out)
    _atomic_write(_pending_path(root, str(out["queue_id"])), out)
    return out


def _job_eligible(item: dict[str, Any], capabilities: dict[str, Any] | None) -> bool:
    caps = capabilities or {}
    min_vram = item.get("min_vram_mb")
    if min_vram is not None:
        vram = caps.get("vram_mb")
        if vram is None or int(vram) < int(min_vram):
            return False
    quantization = item.get("quantization")
    if quantization:
        supported = caps.get("quantization") or caps.get("quantizations") or []
        if supported and quantization not in supported:
            return False
    return True


def _lookup_pending(root: Path, job_id: str) -> tuple[Path, dict[str, Any]] | None:
    direct = _pending_path(root, job_id)
    if direct.is_file():
        return direct, _read_json(direct)
    for path in pending_dir(root).glob("*.json"):
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError, QueueStoreError):
            continue
        if str(data.get("queue_id")) == job_id or str(data.get("run_id")) == job_id:
            return path, data
    return None


def claim(
    root: Path,
    runner_id: str,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Claim the next eligible PENDING job for *runner_id*."""
    pending = pending_dir(root)
    now = _now_dt()
    candidates: list[tuple[int, str, str]] = []
    for path in pending.glob("*.json"):
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError, QueueStoreError):
            continue
        if data.get("status") != STATUS_PENDING:
            continue
        retry_after = data.get("retry_after")
        if retry_after:
            with contextlib.suppress(ValueError, TypeError):
                if parse_dt(str(retry_after)) > now:
                    continue
        if not _job_eligible(data, capabilities):
            continue
        pri = int(data.get("priority", 100))
        created = str(data.get("created_at", ""))
        queue_id = str(data.get("queue_id", path.stem))
        candidates.append((pri, created, queue_id))

    candidates.sort(key=lambda item: (item[0], item[1]))
    for _, _, queue_id in candidates:
        src = _pending_path(root, queue_id)
        lock = _lock_path(root, queue_id, runner_id)
        try:
            os.rename(src, lock)
        except FileNotFoundError:
            continue
        except OSError:
            continue

        try:
            data = _read_json(lock)
            if data.get("status") != STATUS_PENDING:
                with contextlib.suppress(OSError):
                    os.rename(lock, src)
                continue
            now_str = utc_now()
            data["status"] = STATUS_CLAIMED
            data["claimed_by"] = runner_id
            data["claimed_at"] = now_str
            data["last_heartbeat"] = now_str
            data["updated_at"] = now_str
            fd, tmp = tempfile.mkstemp(dir=pending, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(data, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                os.replace(tmp, lock)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except BaseException:
            with contextlib.suppress(OSError):
                os.rename(lock, src)
            raise

        os.rename(lock, src)
        runner = get_runner(root, runner_id)
        if runner is not None:
            runner["status"] = RUNNER_ACTIVE
            runner["current_claim"] = queue_id
            runner["last_heartbeat"] = utc_now()
            _atomic_write(_runner_path(root, runner_id), runner)
        return data
    return None


def heartbeat(root: Path, job_id: str, runner_id: str) -> dict[str, Any]:
    found = _lookup_pending(root, job_id)
    if found is None:
        raise QueueStoreError(f"unknown in-flight job: {job_id}")
    path, data = found
    if data.get("claimed_by") != runner_id:
        raise QueueStoreError("job is not claimed by this runner")
    if data.get("status") not in {STATUS_CLAIMED, STATUS_IN_PROGRESS}:
        raise QueueStoreError(f"job {job_id} is not in-flight")
    now_str = utc_now()
    data["status"] = STATUS_IN_PROGRESS
    data["last_heartbeat"] = now_str
    data.setdefault("started_at", now_str)
    data["updated_at"] = now_str
    _atomic_write(path, data)
    runner = get_runner(root, runner_id)
    if runner is not None:
        runner["last_heartbeat"] = now_str
        runner["status"] = RUNNER_ACTIVE
        _atomic_write(_runner_path(root, runner_id), runner)
    return data


def complete(root: Path, job_id: str, runner_id: str) -> dict[str, Any]:
    found = _lookup_pending(root, job_id)
    if found is None:
        raise QueueStoreError(f"unknown in-flight job: {job_id}")
    src, data = found
    if data.get("claimed_by") != runner_id:
        raise QueueStoreError("job is not claimed by this runner")
    now_str = utc_now()
    data["status"] = STATUS_COMPLETE
    data["completed_at"] = now_str
    data["updated_at"] = now_str
    queue_id = str(data.get("queue_id", src.stem))
    _atomic_write(_completed_path(root, queue_id), data)
    with contextlib.suppress(FileNotFoundError):
        src.unlink()
    runner = get_runner(root, runner_id)
    if runner is not None:
        runner["status"] = RUNNER_IDLE
        runner["current_claim"] = None
        runner["last_heartbeat"] = now_str
        _atomic_write(_runner_path(root, runner_id), runner)
    return data


def fail(root: Path, job_id: str, error: str) -> dict[str, Any]:
    found = _lookup_pending(root, job_id)
    if found is None:
        raise QueueStoreError(f"unknown in-flight job: {job_id}")
    src, data = found
    now_str = utc_now()
    attempt = int(data.get("attempt_count", 0))
    max_att = int(data.get("max_attempts", 5))
    data["error_detail"] = error
    runner_id = data.get("claimed_by")

    if attempt < max_att:
        attempt += 1
        data["attempt_count"] = attempt
        data["status"] = STATUS_PENDING
        data["claimed_by"] = None
        data["claimed_at"] = None
        data["started_at"] = None
        data["last_heartbeat"] = None
        retry_dt = _now_dt() + timedelta(minutes=_RETRY_BACKOFF_MINUTES * attempt)
        data["retry_after"] = retry_dt.strftime(_ISO_FMT)
        data["priority"] = int(data.get("priority", 100)) + _RETRY_PRIORITY_BUMP * attempt
        data["updated_at"] = now_str
        _atomic_write(src, data)
    else:
        data["status"] = STATUS_FAILED
        data["completed_at"] = now_str
        data["updated_at"] = now_str
        queue_id = str(data.get("queue_id", src.stem))
        _atomic_write(_completed_path(root, queue_id), data)
        with contextlib.suppress(FileNotFoundError):
            src.unlink()

    if isinstance(runner_id, str):
        runner = get_runner(root, runner_id)
        if runner is not None:
            runner["status"] = RUNNER_IDLE
            runner["current_claim"] = None
            _atomic_write(_runner_path(root, runner_id), runner)
    return data


def requeue(root: Path, job_id: str) -> dict[str, Any]:
    found = _lookup_pending(root, job_id)
    if found is not None:
        src, data = found
        runner_id = data.get("claimed_by")
        data["status"] = STATUS_PENDING
        data["claimed_by"] = None
        data["claimed_at"] = None
        data["started_at"] = None
        data["last_heartbeat"] = None
        data["retry_after"] = None
        data["updated_at"] = utc_now()
        _atomic_write(src, data)
        if isinstance(runner_id, str):
            runner = get_runner(root, runner_id)
            if runner is not None and runner.get("current_claim") in {
                data.get("queue_id"),
                data.get("run_id"),
                job_id,
            }:
                runner["status"] = RUNNER_IDLE
                runner["current_claim"] = None
                _atomic_write(_runner_path(root, runner_id), runner)
        return data

    completed = None
    for path in completed_dir(root).glob("*.json"):
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError, QueueStoreError):
            continue
        if str(data.get("queue_id")) == job_id or str(data.get("run_id")) == job_id:
            completed = (path, data)
            break
    if completed is None:
        raise QueueStoreError(f"unknown job: {job_id}")
    path, data = completed
    data["status"] = STATUS_PENDING
    data["claimed_by"] = None
    data["claimed_at"] = None
    data["started_at"] = None
    data["completed_at"] = None
    data["last_heartbeat"] = None
    data["retry_after"] = None
    data["updated_at"] = utc_now()
    queue_id = str(data["queue_id"])
    _atomic_write(_pending_path(root, queue_id), data)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    return data


def reap_stale_claims(root: Path, timeout_seconds: int) -> list[str]:
    now = _now_dt()
    timeout = timedelta(seconds=timeout_seconds)
    reaped: list[str] = []
    for path in pending_dir(root).glob("*.json"):
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError, QueueStoreError):
            continue
        if data.get("status") not in {STATUS_CLAIMED, STATUS_IN_PROGRESS}:
            continue
        stamp = data.get("last_heartbeat") or data.get("claimed_at")
        if not stamp:
            continue
        try:
            claimed_dt = parse_dt(str(stamp))
        except ValueError:
            continue
        if now - claimed_dt < timeout:
            continue
        queue_id = str(data.get("queue_id", path.stem))
        runner_id = data.get("claimed_by")
        now_str = utc_now()
        data["status"] = STATUS_PENDING
        data["claimed_by"] = None
        data["claimed_at"] = None
        data["started_at"] = None
        data["last_heartbeat"] = None
        data["updated_at"] = now_str
        with contextlib.suppress(OSError, QueueStoreError):
            _atomic_write(path, data)
            reaped.append(queue_id)
        if isinstance(runner_id, str):
            runner = get_runner(root, runner_id)
            if runner is not None:
                runner["status"] = RUNNER_DEAD
                runner["current_claim"] = None
                _atomic_write(_runner_path(root, runner_id), runner)
    return reaped


def list_pending(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in pending_dir(root).glob("*.json"):
        with contextlib.suppress(OSError, json.JSONDecodeError, QueueStoreError):
            items.append(_read_json(path))
    return items


def list_completed(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in completed_dir(root).glob("*.json"):
        with contextlib.suppress(OSError, json.JSONDecodeError, QueueStoreError):
            items.append(_read_json(path))
    return items


def write_submission(
    root: Path,
    runner_id: str,
    run_id: str,
    envelope: dict[str, Any],
) -> Path:
    dest = root / "submissions" / runner_id / run_id / "envelope.json"
    _atomic_write(dest, envelope)
    result = envelope.get("result")
    if isinstance(result, dict):
        _atomic_write(dest.with_name("result.json"), result)
    return dest.parent

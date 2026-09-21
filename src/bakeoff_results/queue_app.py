"""HTTP routing for the distributed-worker queue API."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bakeoff_results.queue_auth import AuthError, mint_token, parse_token, require_admin
from bakeoff_results.queue_signing import SigningError, verify_result
from bakeoff_results.queue_store import (
    add_whitelist_key,
    claim,
    complete,
    enqueue,
    fail,
    get_runner,
    heartbeat,
    list_completed,
    list_pending,
    list_runners,
    load_whitelist,
    reap_stale_claims,
    remove_whitelist_key,
    requeue,
    set_runner_status,
    upsert_runner,
    write_submission,
)
from bakeoff_results.validate import BundleValidationError, validate_result

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DEFAULT_TOKEN_TTL_S = 86400
DEFAULT_HEARTBEAT_TTL_S = 120
DEFAULT_HEARTBEAT_INTERVAL_S = 30


@dataclass
class QueueSettings:
    data_dir: Path
    secret: bytes
    admin_token: str
    heartbeat_ttl_s: int = DEFAULT_HEARTBEAT_TTL_S
    token_ttl_s: int = DEFAULT_TOKEN_TTL_S


@dataclass
class AppResponse:
    status: int
    body: bytes
    content_type: str = "application/json"


def _json(status: int, payload: dict[str, Any]) -> AppResponse:
    return AppResponse(
        status,
        json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n",
    )


def _error(status: int, message: str) -> AppResponse:
    return _json(status, {"error": message})


def _read_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError("JSON body must be an object")
    return data


def runner_id_for_key(public_key: str) -> str:
    return hashlib.sha256(public_key.encode()).hexdigest()[:32]


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items()}


def _require_runner(settings: QueueSettings, headers: dict[str, str]) -> str:
    from bakeoff_results.queue_auth import bearer_token

    token = bearer_token(headers)
    if token is None:
        raise AuthError("runner token required")
    return parse_token(settings.secret, token)


def dispatch(
    settings: QueueSettings,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
) -> AppResponse:
    headers = _normalize_headers(headers)
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    method = method.upper()
    reap_stale_claims(settings.data_dir, settings.heartbeat_ttl_s)

    try:
        if method == "GET" and route == "/health":
            return _json(200, {"ok": True})
        if method == "GET" and route == "/runners":
            from bakeoff_results.queue_dashboard import DASHBOARD_HTML

            return AppResponse(200, DASHBOARD_HTML.encode(), "text/html; charset=utf-8")
        if method == "POST" and route == "/api/runners/register":
            return _register(settings, _read_json(body))
        if method == "GET" and route == "/api/runners":
            require_admin(headers, settings.admin_token)
            return _json(200, {"runners": list_runners(settings.data_dir)})
        if method == "POST" and route == "/api/admin/keys":
            require_admin(headers, settings.admin_token)
            return _admin_keys(settings, _read_json(body))
        if method == "GET" and route == "/api/admin/keys":
            require_admin(headers, settings.admin_token)
            return _json(200, {"public_keys": load_whitelist(settings.data_dir)})
        if method == "POST" and route == "/api/queue/claim":
            return _claim(settings, headers, _read_json(body))
        if method == "POST" and route == "/api/queue/enqueue":
            require_admin(headers, settings.admin_token)
            return _enqueue(settings, _read_json(body))
        if method == "GET" and route == "/api/queue":
            require_admin(headers, settings.admin_token)
            return _list_queue(settings, parse_qs(parsed.query))
        match = re.fullmatch(r"/api/queue/([^/]+)/(heartbeat|submit|requeue|fail)", route)
        if match:
            job_id = match.group(1)
            action = match.group(2)
            if not SAFE_ID.fullmatch(job_id):
                return _error(400, "invalid job id")
            if action == "heartbeat" and method == "POST":
                return _heartbeat(settings, headers, job_id)
            if action == "submit" and method == "POST":
                return _submit(settings, headers, job_id, _read_json(body))
            if action == "fail" and method == "POST":
                return _fail(settings, headers, job_id, _read_json(body))
            if action == "requeue" and method == "POST":
                require_admin(headers, settings.admin_token)
                return _json(200, {"job": requeue(settings.data_dir, job_id)})
        match = re.fullmatch(r"/api/admin/runners/([^/]+)/status", route)
        if match and method == "POST":
            require_admin(headers, settings.admin_token)
            runner_id = match.group(1)
            if not SAFE_ID.fullmatch(runner_id):
                return _error(400, "invalid runner id")
            payload = _read_json(body)
            status = str(payload.get("status", ""))
            return _json(200, {"runner": set_runner_status(settings.data_dir, runner_id, status)})
        return _error(404, "not found")
    except AuthError as exc:
        return _error(401, str(exc))
    except SigningError as exc:
        return _error(400, str(exc))
    except BundleValidationError as exc:
        return _error(400, str(exc))
    except (TypeError, ValueError) as exc:
        return _error(400, str(exc))


def _register(settings: QueueSettings, payload: dict[str, Any]) -> AppResponse:
    public_key = payload.get("public_key")
    if not isinstance(public_key, str) or not public_key.strip():
        return _error(400, "public_key is required")
    public_key = public_key.strip()
    if public_key not in load_whitelist(settings.data_dir):
        return _error(403, "public key is not on the submission whitelist")
    runner_id = str(payload.get("runner_id") or runner_id_for_key(public_key))
    if not SAFE_ID.fullmatch(runner_id):
        return _error(400, "invalid runner_id")
    capabilities = payload.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, dict):
        return _error(400, "capabilities must be an object")
    runner = upsert_runner(
        settings.data_dir,
        {
            "runner_id": runner_id,
            "public_key": public_key,
            "hostname": payload.get("hostname"),
            "process_id": payload.get("process_id"),
            "effective_user": payload.get("effective_user"),
            "description": payload.get("description"),
            "capabilities": capabilities or {},
            "status": "IDLE",
        },
    )
    token, exp = mint_token(settings.secret, runner_id, settings.token_ttl_s)
    return _json(
        200,
        {
            "runner": runner,
            "token": token,
            "expires_at": exp,
            "heartbeat_interval_s": DEFAULT_HEARTBEAT_INTERVAL_S,
            "heartbeat_ttl_s": settings.heartbeat_ttl_s,
        },
    )


def _admin_keys(settings: QueueSettings, payload: dict[str, Any]) -> AppResponse:
    action = payload.get("action")
    public_key = payload.get("public_key")
    if action not in {"add", "remove"}:
        return _error(400, "action must be add or remove")
    if not isinstance(public_key, str) or not public_key.strip():
        return _error(400, "public_key is required")
    public_key = public_key.strip()
    if action == "add":
        keys = add_whitelist_key(settings.data_dir, public_key)
    else:
        keys = remove_whitelist_key(settings.data_dir, public_key)
        for runner in list_runners(settings.data_dir):
            if runner.get("public_key") == public_key:
                set_runner_status(settings.data_dir, str(runner["runner_id"]), "DEAD")
    return _json(200, {"public_keys": keys})


def _claim(
    settings: QueueSettings,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> AppResponse:
    runner_id = _require_runner(settings, headers)
    runner = get_runner(settings.data_dir, runner_id)
    if runner is None:
        return _error(403, "runner is not registered")
    if runner.get("status") == "DEAD":
        return _error(403, "runner is revoked")
    if runner.get("status") == "PAUSED":
        return _error(403, "runner is paused")
    capabilities = payload.get("capabilities")
    stored_caps = runner.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = stored_caps if isinstance(stored_caps, dict) else {}
    job = claim(settings.data_dir, runner_id, capabilities)
    if job is None:
        return AppResponse(204, b"", "text/plain")
    return _json(
        200,
        {
            "job": job,
            "heartbeat_interval_s": DEFAULT_HEARTBEAT_INTERVAL_S,
            "heartbeat_ttl_s": settings.heartbeat_ttl_s,
        },
    )


def _enqueue(settings: QueueSettings, payload: dict[str, Any]) -> AppResponse:
    job = enqueue(settings.data_dir, payload)
    return _json(201, {"job": job})


def _list_queue(settings: QueueSettings, query: dict[str, list[str]]) -> AppResponse:
    pending = list_pending(settings.data_dir)
    completed = list_completed(settings.data_dir)
    status_filter = query.get("status", [None])[0]
    jobs = pending + completed
    if status_filter:
        jobs = [job for job in jobs if job.get("status") == status_filter]
    inflight = [job for job in pending if job.get("status") in {"CLAIMED", "IN_PROGRESS"}]
    return _json(
        200,
        {
            "depth": sum(1 for job in pending if job.get("status") == "PENDING"),
            "in_flight": len(inflight),
            "jobs": jobs,
        },
    )


def _heartbeat(settings: QueueSettings, headers: dict[str, str], job_id: str) -> AppResponse:
    runner_id = _require_runner(settings, headers)
    return _json(200, {"job": heartbeat(settings.data_dir, job_id, runner_id)})


def _fail(
    settings: QueueSettings,
    headers: dict[str, str],
    job_id: str,
    payload: dict[str, Any],
) -> AppResponse:
    runner_id = _require_runner(settings, headers)
    error = payload.get("error") or payload.get("error_detail") or "runner reported failure"
    if not isinstance(error, str) or not error.strip():
        return _error(400, "error is required")
    return _json(200, {"job": fail(settings.data_dir, job_id, error.strip(), runner_id)})


def _submit(
    settings: QueueSettings,
    headers: dict[str, str],
    job_id: str,
    payload: dict[str, Any],
) -> AppResponse:
    runner_id = _require_runner(settings, headers)
    runner = get_runner(settings.data_dir, runner_id)
    if runner is None:
        return _error(403, "runner is not registered")
    wrapped = payload.get("envelope")
    envelope = wrapped if isinstance(wrapped, dict) else payload
    result = verify_result(envelope, str(runner["public_key"]))
    sig = envelope.get("sig")
    if isinstance(sig, dict) and sig.get("runner_id") not in {None, runner_id}:
        return _error(400, "envelope runner_id does not match bearer")
    validate_result(result)
    dest = write_submission(settings.data_dir, runner_id, str(result["run_id"]), envelope)
    job = complete(settings.data_dir, job_id, runner_id)
    return _json(200, {"job": job, "stored": str(dest)})

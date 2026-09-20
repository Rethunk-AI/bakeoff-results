"""HMAC runner tokens and admin-bearer checks for the queue API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from bakeoff_results.queue_store import data_dir


class AuthError(ValueError):
    """Raised when a runner or admin token is missing or invalid."""


def load_or_create_secret(root: Path | None = None) -> bytes:
    """Return the HMAC secret, creating a persistent one in *root* when unset."""
    env = os.environ.get("BAKEOFF_QUEUE_SECRET")
    if env:
        return env.encode()
    base = root if root is not None else data_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / "queue_secret"
    if path.is_file():
        return path.read_bytes().strip()
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    path.chmod(0o600)
    return secret


def load_or_create_admin_token(root: Path | None = None) -> str:
    env = os.environ.get("BAKEOFF_QUEUE_ADMIN_TOKEN")
    if env:
        return env
    base = root if root is not None else data_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / "admin_token"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(0o600)
    return token


def mint_token(secret: bytes, runner_id: str, ttl_s: int) -> tuple[str, int]:
    exp = int(time.time()) + ttl_s
    payload = json.dumps({"runner_id": runner_id, "exp": exp}, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    mac = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{mac}", exp


def parse_token(secret: bytes, token: str) -> str:
    try:
        payload_b64, mac = token.split(".", 1)
    except ValueError as exc:
        raise AuthError("malformed token") from exc
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(padded.encode())
        payload = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed token payload") from exc
    expected = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise AuthError("invalid token signature")
    exp = payload.get("exp")
    runner_id = payload.get("runner_id")
    if not isinstance(runner_id, str) or not runner_id:
        raise AuthError("token missing runner_id")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise AuthError("token expired")
    return runner_id


def bearer_token(headers: dict[str, str]) -> str | None:
    auth = headers.get("Authorization") or headers.get("authorization")
    if not auth:
        return None
    kind, _, rest = auth.partition(" ")
    if kind.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip()


def require_admin(headers: dict[str, str], admin_token: str) -> None:
    token = bearer_token(headers)
    if token is None or not hmac.compare_digest(token, admin_token):
        raise AuthError("admin token required")

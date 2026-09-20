"""Ed25519 envelopes for queue result submission.

Matches Rethunk-AI/bakeoff ``bench.signing`` so a worker can submit the same
``{result, sig}`` document it already produces for local publication.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class SigningError(ValueError):
    """Raised when signature verification fails."""


def canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def encode_public_key(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


def generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    return private_key, encode_public_key(private_key.public_key())


def sign_result(
    result: dict[str, Any],
    private_key: Ed25519PrivateKey,
    runner_id: str,
) -> dict[str, Any]:
    canonical = canonical_json(result)
    sha256_bytes = hashlib.sha256(canonical).digest()
    signature_b64 = base64.b64encode(private_key.sign(sha256_bytes)).decode()
    signed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "result": result,
        "sig": {
            "sha256": sha256_bytes.hex(),
            "signature": signature_b64,
            "runner_id": runner_id,
            "signed_at": signed_at,
        },
    }


def verify_result(envelope: dict[str, Any], public_key_b64: str) -> dict[str, Any]:
    if "result" not in envelope or "sig" not in envelope:
        raise SigningError("envelope missing required keys: 'result' and/or 'sig'")
    result = envelope["result"]
    sig = envelope["sig"]
    if not isinstance(result, dict) or not isinstance(sig, dict):
        raise SigningError("envelope result and sig must be objects")

    canonical = canonical_json(result)
    sha256_bytes = hashlib.sha256(canonical).digest()
    recorded_hex = sig.get("sha256", "")
    if sha256_bytes.hex() != recorded_hex:
        raise SigningError("SHA256 mismatch")

    try:
        signature_bytes = base64.b64decode(sig["signature"])
        raw_pub = base64.b64decode(public_key_b64)
        public_key = Ed25519PublicKey.from_public_bytes(raw_pub)
    except Exception as exc:
        raise SigningError(f"invalid signature material: {exc}") from exc

    try:
        public_key.verify(signature_bytes, sha256_bytes)
    except InvalidSignature as exc:
        raise SigningError("Ed25519 signature verification failed") from exc
    return result

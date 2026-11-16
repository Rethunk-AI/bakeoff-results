"""Regression tests for the validator's signer gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bakeoff_results.validate import BundleValidationError, validate_bundle


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rekor_signature() -> dict:
    return {
        "verificationMaterial": {
            "tlogEntries": [
                {
                    "logIndex": 42,
                    "logId": {"keyId": "c0d23d6ad406973f9559f3ba2d1ca01f84147d8f"},
                    "integratedTime": 1700000000,
                    "canonicalizedBody": "eyJhcGlWZXJzaW9uIjoiMC4wLjEifQ==",
                    "inclusionPromise": {"signedEntryTimestamp": "MEUCIQDtest=="},
                }
            ]
        }
    }


def write_bundle(
    root: Path,
    *,
    signer: object = None,
    signature: dict | None = None,
    model_ids: object = None,
    name: str = "bundle",
) -> Path:
    bundle = root / name
    bundle.mkdir(parents=True)
    result = {
        "run_id": "run-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "provenance": {"source_repository": "Rethunk-AI/bakeoff", "source_commit": "abc123"},
        "model_ids": ["model-a"] if model_ids is None else model_ids,
    }
    (bundle / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (bundle / "summary.md").write_text("# summary\n", encoding="utf-8")
    manifest = {
        "schema_version": "bakeoff-results/v1",
        "files": {
            "result.json": _sha256(bundle / "result.json"),
            "summary.md": _sha256(bundle / "summary.md"),
        },
    }
    if signer is not None:
        manifest["signer"] = signer
    if signature is not None:
        (bundle / "signature.sigstore.json").write_text(json.dumps(signature), encoding="utf-8")
        manifest["files"]["signature.sigstore.json"] = _sha256(bundle / "signature.sigstore.json")
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


SIGNER = {
    "identity": "github-actions[bot]",
    "issuer": "https://token.actions.githubusercontent.com",
}


def test_manifest_without_signer_is_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer=None, signature=rekor_signature())

    with pytest.raises(BundleValidationError, match="signer"):
        validate_bundle(bundle)

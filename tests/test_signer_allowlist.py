"""Signer allowlist checks for bakeoff result bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bakeoff_results.validate import BundleValidationError, validate_bundle

ALLOWED_SIGNER = {
    "identity": "github-actions[bot]",
    "issuer": "https://token.actions.githubusercontent.com",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bundle(tmp_path: Path, signer: object) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    result = {
        "run_id": "run-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "provenance": {
            "source_repository": "https://github.com/Rethunk-AI/bakeoff",
            "source_commit": "abc123",
            "git": {"sha": "abc123"},
        },
        "model_ids": ["model-a"],
    }
    (bundle / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (bundle / "summary.md").write_text("# summary\n", encoding="utf-8")
    files = {
        "result.json": _sha256(bundle / "result.json"),
        "summary.md": _sha256(bundle / "summary.md"),
    }
    manifest = {"schema_version": "bakeoff-results/v1", "files": files, "signer": signer}
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


def test_allowed_signer(tmp_path: Path) -> None:
    validate_bundle(_write_bundle(tmp_path, ALLOWED_SIGNER))


def test_unknown_signer(tmp_path: Path) -> None:
    signer = {
        "identity": "not-allowlisted@example.com",
        "issuer": "https://token.actions.githubusercontent.com",
    }
    with pytest.raises(BundleValidationError, match="not allowlisted"):
        validate_bundle(_write_bundle(tmp_path, signer))


def test_malformed_signer(tmp_path: Path) -> None:
    with pytest.raises(BundleValidationError, match="signer"):
        validate_bundle(_write_bundle(tmp_path, "github-actions[bot]"))

"""Signer allowlist checks: subject, repository and workflow all must bind."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bakeoff_results.validate import BundleValidationError, validate_bundle

ALLOWED_SUBJECT = "repo:Rethunk-AI/bakeoff:ref:refs/heads/main"
ALLOWED_WORKFLOW = ".github/workflows/package-results.yml"
OIDC = "https://token.actions.githubusercontent.com"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_bundle(root: Path, signer: dict) -> Path:
    bundle = root / "bundle"
    bundle.mkdir(parents=True)
    result = {
        "run_id": "run-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "provenance": {"source_repository": "Rethunk-AI/bakeoff", "source_commit": "abc123"},
        "model_ids": ["model-a"],
    }
    (bundle / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (bundle / "summary.md").write_text("# summary\n", encoding="utf-8")
    manifest = {
        "schema_version": "bakeoff-results/v1",
        "files": {
            "result.json": _sha256(bundle / "result.json"),
            "summary.md": _sha256(bundle / "summary.md"),
        },
        "signer": signer,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


def signer(**overrides: object) -> dict:
    fields = {
        "identity": "github-actions[bot]",
        "issuer": OIDC,
        "repository": "Rethunk-AI/bakeoff",
        "subject": ALLOWED_SUBJECT,
        "workflow": ALLOWED_WORKFLOW,
    }
    fields.update(overrides)
    return fields


def test_matching_repository_subject_and_workflow_is_allowed(tmp_path: Path) -> None:
    validate_bundle(write_bundle(tmp_path, signer()))


def test_allowlisted_subject_with_foreign_repository_is_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer(repository="attacker/fork"))

    with pytest.raises(BundleValidationError, match="not allowlisted"):
        validate_bundle(bundle)


def test_allowlisted_repository_with_unlisted_workflow_is_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer(workflow=".github/workflows/other.yml"))

    with pytest.raises(BundleValidationError, match="not allowlisted"):
        validate_bundle(bundle)


def test_allowlisted_repository_without_a_subject_is_rejected(tmp_path: Path) -> None:
    bare = signer()
    del bare["subject"]
    bundle = write_bundle(tmp_path, bare)

    with pytest.raises(BundleValidationError, match="not allowlisted"):
        validate_bundle(bundle)

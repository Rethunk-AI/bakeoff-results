"""Regression tests for the validator's signer gate and Rekor evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from bakeoff_results import validate as validate_module
from bakeoff_results.validate import (
    BundleValidationError,
    validate_bundle,
    validate_result,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    files: dict[str, object] = {
        "result.json": _sha256(bundle / "result.json"),
        "summary.md": _sha256(bundle / "summary.md"),
    }
    if signature is not None:
        (bundle / "signature.sigstore.json").write_text(json.dumps(signature), encoding="utf-8")
        files["signature.sigstore.json"] = _sha256(bundle / "signature.sigstore.json")
    manifest: dict[str, object] = {"schema_version": "bakeoff-results/v1", "files": files}
    if signer is not None:
        manifest["signer"] = signer
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


SIGNER = {
    "identity": "github-actions[bot]",
    "issuer": "https://token.actions.githubusercontent.com",
    "repository": "Rethunk-AI/bakeoff",
    "subject": "repo:Rethunk-AI/bakeoff:ref:refs/heads/main",
    "workflow": ".github/workflows/package-results.yml",
}


def test_manifest_without_signer_is_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer=None, signature=rekor_signature())

    with pytest.raises(BundleValidationError, match="signer"):
        validate_bundle(bundle)


def test_empty_verification_material_is_not_rekor_evidence(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer=SIGNER, signature={"verificationMaterial": {}})

    with pytest.raises(BundleValidationError, match="Rekor"):
        validate_bundle(bundle)


def test_rekor_entry_missing_required_fields_is_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(
        tmp_path,
        signer=SIGNER,
        signature={"verificationMaterial": {"tlogEntries": [{"logIndex": 1}]}},
    )

    with pytest.raises(BundleValidationError, match="malformed"):
        validate_bundle(bundle)


def test_rekor_entry_without_inclusion_proof_is_rejected(tmp_path: Path) -> None:
    entry = rekor_signature()["verificationMaterial"]["tlogEntries"][0]
    del entry["inclusionPromise"]
    bundle = write_bundle(
        tmp_path,
        signer=SIGNER,
        signature={"verificationMaterial": {"tlogEntries": [entry]}},
    )

    with pytest.raises(BundleValidationError, match="malformed"):
        validate_bundle(bundle)


def test_well_formed_rekor_entry_is_accepted(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer=SIGNER, signature=rekor_signature())

    validate_bundle(bundle)


def test_require_signature_rejects_a_bundle_without_a_signature(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer=SIGNER)

    assert validate_module.main(["--require-signature", str(bundle)]) == 1


def test_require_signature_accepts_a_well_formed_rekor_entry(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer=SIGNER, signature=rekor_signature())

    assert validate_module.main(["--require-signature", str(bundle)]) == 0


def test_empty_model_ids_is_rejected() -> None:
    result = {
        "run_id": "run-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "provenance": {"source_repository": "Rethunk-AI/bakeoff", "source_commit": "abc123"},
        "model_ids": [],
    }

    with pytest.raises(BundleValidationError, match="model_ids"):
        validate_result(result)


def test_non_string_model_ids_is_rejected() -> None:
    result = {
        "run_id": "run-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "provenance": {"source_repository": "Rethunk-AI/bakeoff", "source_commit": "abc123"},
        "model_ids": [123],
    }

    with pytest.raises(BundleValidationError, match="model_ids"):
        validate_result(result)


def test_signers_policy_is_found_from_the_working_directory(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    shutil.copy(REPO_ROOT / "signers.yaml", repo / "signers.yaml")
    bundle = write_bundle(tmp_path, signer=SIGNER, signature=rekor_signature())

    monkeypatch.chdir(repo)
    validate_bundle(bundle)


def test_signers_policy_is_not_looked_up_beside_the_package(tmp_path: Path, monkeypatch) -> None:
    # An installed layout has no signers.yaml next to site-packages, so the
    # policy has to come from the repo root rather than the package directory.
    package = tmp_path / "lib" / "site-packages" / "bakeoff_results"
    shutil.copytree(REPO_ROOT / "src" / "bakeoff_results", package)
    repo = tmp_path / "checkout"
    repo.mkdir()
    shutil.copy(REPO_ROOT / "signers.yaml", repo / "signers.yaml")
    bundle = write_bundle(tmp_path, signer=SIGNER, signature=rekor_signature())

    spec = importlib.util.spec_from_file_location("installed_validate", package / "validate.py")
    assert spec is not None and spec.loader is not None
    installed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installed)
    assert Path(installed.__file__ or "").resolve().parent == package.resolve()
    assert not (package.parent / "signers.yaml").exists()

    monkeypatch.chdir(repo)
    installed.validate_bundle(bundle)


def test_explicit_signers_path_wins_over_the_working_directory(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    policy = tmp_path / "policy" / "signers.yaml"
    policy.parent.mkdir()
    shutil.copy(REPO_ROOT / "signers.yaml", policy)
    bundle = write_bundle(tmp_path, signer=SIGNER, signature=rekor_signature())

    monkeypatch.chdir(repo)
    assert not (repo / "signers.yaml").exists()

    validate_bundle(bundle, policy)


def test_ci_validate_step_requires_signatures() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    validate_steps = [line for line in workflow.splitlines() if "bakeoff_results.validate" in line]

    assert validate_steps
    assert all("--require-signature" in line for line in validate_steps)

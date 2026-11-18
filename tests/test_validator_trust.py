"""Regression tests for the validator's signer gate and Rekor evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from bakeoff_results import validate as validate_module
from bakeoff_results.validate import (
    BundleValidationError,
    _load_signers,
    _parse_yaml_subset,
    _runner_fingerprints,
    discover_bundles,
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
    # dataclasses resolves string annotations through sys.modules[cls.__module__], which
    # this module is not registered in unless we put it there ourselves.
    monkeypatch.setitem(sys.modules, spec.name, installed)
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


def test_yaml_subset_parses_nested_list_keys_and_rejects_malformed_policy() -> None:
    parsed = _parse_yaml_subset("items:\n  - nested:\n      a: b\n  - empty:\nfoo: 1\n")
    assert parsed == {"items": [{"nested": {"a": "b"}}, {"empty": None}], "foo": "1"}

    with pytest.raises(BundleValidationError, match="malformed"):
        _parse_yaml_subset("")
    with pytest.raises(BundleValidationError, match="malformed"):
        _parse_yaml_subset("foo: 1\n- leftover\n")
    with pytest.raises(BundleValidationError, match="malformed"):
        _parse_yaml_subset("foo: 1\n  bar: 2\n")
    with pytest.raises(BundleValidationError, match="malformed"):
        _parse_yaml_subset("no-colon\n")

    with pytest.raises(BundleValidationError, match="allowlist not found"):
        _load_signers(Path("no-such-signers.yaml"))


def test_signers_policy_rejects_wrong_schema_and_non_string_runners(tmp_path: Path) -> None:
    list_root = tmp_path / "list.yaml"
    list_root.write_text("- just-a-list\n", encoding="utf-8")
    with pytest.raises(BundleValidationError, match="malformed"):
        _load_signers(list_root)

    schema = tmp_path / "schema.yaml"
    schema.write_text("schema_version: other\n", encoding="utf-8")
    with pytest.raises(BundleValidationError, match="schema_version"):
        _load_signers(schema)

    bare = tmp_path / "bare.yaml"
    bare.write_text("schema_version: bakeoff-results-signers/v1\n", encoding="utf-8")
    assert _runner_fingerprints(_load_signers(bare)) == set()

    not_list = tmp_path / "runners-obj.yaml"
    not_list.write_text(
        "schema_version: bakeoff-results-signers/v1\nrunners:\n  fingerprint: x\n",
        encoding="utf-8",
    )
    with pytest.raises(BundleValidationError, match="runners must be a list"):
        _runner_fingerprints(_load_signers(not_list))

    not_string = tmp_path / "runners-map.yaml"
    not_string.write_text(
        "schema_version: bakeoff-results-signers/v1\nrunners:\n  - fp: sha256:abc\n",
        encoding="utf-8",
    )
    with pytest.raises(BundleValidationError, match="fingerprint strings"):
        _runner_fingerprints(_load_signers(not_string))


def test_manifest_files_list_shape_and_path_safety(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, signer=SIGNER)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        {"path": "result.json", "sha256": manifest["files"]["result.json"]},
        {"path": "summary.md", "sha256": manifest["files"]["summary.md"]},
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    validate_bundle(bundle)

    manifest["files"] = [{"sha256": "ab"}]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="path and sha256"):
        validate_bundle(bundle)

    manifest["files"] = ["result.json"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="must be objects"):
        validate_bundle(bundle)

    manifest["files"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="object or list"):
        validate_bundle(bundle)

    good = write_bundle(tmp_path, signer=SIGNER, name="safe")
    good_manifest = json.loads((good / "manifest.json").read_text(encoding="utf-8"))
    digest = good_manifest["files"]["result.json"]
    good_manifest["files"]["../outside.json"] = digest
    (good / "manifest.json").write_text(json.dumps(good_manifest), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="unsafe"):
        validate_bundle(good)

    missing_hash = write_bundle(tmp_path, signer=SIGNER, name="nohash")
    nohash = json.loads((missing_hash / "manifest.json").read_text(encoding="utf-8"))
    del nohash["files"]["summary.md"]
    (missing_hash / "manifest.json").write_text(json.dumps(nohash), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="missing sha256"):
        validate_bundle(missing_hash)

    ghost = write_bundle(tmp_path, signer=SIGNER, name="ghost")
    ghost_manifest = json.loads((ghost / "manifest.json").read_text(encoding="utf-8"))
    ghost_manifest["files"]["extra.bin"] = digest
    (ghost / "manifest.json").write_text(json.dumps(ghost_manifest), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="missing file"):
        validate_bundle(ghost)

    mismatch = write_bundle(tmp_path, signer=SIGNER, name="mismatch")
    mismatch_manifest = json.loads((mismatch / "manifest.json").read_text(encoding="utf-8"))
    mismatch_manifest["bundle"] = {"run_id": "other", "timestamp": "nope"}
    (mismatch / "manifest.json").write_text(json.dumps(mismatch_manifest), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="run_id"):
        validate_bundle(mismatch)


def test_result_accepts_git_sha_and_rejects_nonfinite_or_shapeless_models() -> None:
    git_only = {
        "run_id": "run-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "provenance": {"git": {"sha": "abc123"}},
        "model_metadata": [{"id": "model-a"}],
    }
    validate_result(git_only)

    with pytest.raises(BundleValidationError, match="source_repository"):
        validate_result(
            {
                "run_id": "run-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "provenance": {},
                "model_ids": ["model-a"],
            }
        )
    with pytest.raises(BundleValidationError, match="source_commit"):
        validate_result(
            {
                "run_id": "run-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "provenance": {"source_repository": "Rethunk-AI/bakeoff"},
                "model_ids": ["model-a"],
            }
        )
    with pytest.raises(BundleValidationError, match="must be objects"):
        validate_result(
            {
                "run_id": "run-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "provenance": {"source_repository": "org/repo", "source_commit": "abc"},
                "models": ["model-a"],
            }
        )
    with pytest.raises(BundleValidationError, match="id or model_id"):
        validate_result(
            {
                "run_id": "run-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "provenance": {"source_repository": "org/repo", "source_commit": "abc"},
                "models": [{"name": "x"}],
            }
        )
    with pytest.raises(BundleValidationError, match="finite"):
        validate_result(
            {
                "run_id": "run-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "provenance": {"source_repository": "org/repo", "source_commit": "abc"},
                "model_ids": ["model-a"],
                "score": float("inf"),
            }
        )
    with pytest.raises(BundleValidationError, match="finite"):
        validate_result(
            {
                "run_id": "run-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "provenance": {"source_repository": "org/repo", "source_commit": "abc"},
                "model_ids": ["model-a"],
                "model_scores": [{"partial_score": float("nan")}],
            }
        )


def test_signer_identity_binding_and_scan_discovery(tmp_path: Path) -> None:
    policy = tmp_path / "signers.yaml"
    policy.write_text(
        "schema_version: bakeoff-results-signers/v1\n"
        "trusted_repositories:\n"
        "  - repository: Rethunk-AI/bakeoff\n"
        "    issuer: https://token.actions.githubusercontent.com\n"
        "    identity: github-actions[bot]\n"
        "    subjects:\n"
        "      - repo:Rethunk-AI/bakeoff:ref:refs/heads/main\n",
        encoding="utf-8",
    )
    wrong_id = write_bundle(
        tmp_path,
        signer={**SIGNER, "identity": "imposter[bot]"},
        name="wrong-id",
    )
    with pytest.raises(BundleValidationError, match="not allowlisted"):
        validate_bundle(wrong_id, policy)

    empty_issuer = write_bundle(tmp_path, signer={**SIGNER, "issuer": "  "}, name="issuer")
    with pytest.raises(BundleValidationError, match="issuer"):
        validate_bundle(empty_issuer, policy)

    bad_kind = write_bundle(tmp_path, signer={**SIGNER, "kind": 1}, name="kind")
    with pytest.raises(BundleValidationError, match="kind"):
        validate_bundle(bad_kind, policy)

    no_identity = write_bundle(
        tmp_path,
        signer={"kind": "oidc"},
        name="noid",
    )
    with pytest.raises(BundleValidationError, match="identity, subject, or repository"):
        validate_bundle(no_identity, policy)

    bad_policy = write_bundle(tmp_path, signer={**SIGNER, "policy": 1}, name="pol")
    with pytest.raises(BundleValidationError, match="policy"):
        validate_bundle(bad_policy, policy)

    ok = write_bundle(tmp_path, signer=SIGNER, name="ok")
    assert discover_bundles([ok / "manifest.json"]) == [ok]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert validate_module.main(["--scan", "--allow-empty", str(empty)]) == 0
    assert validate_module.main(["--scan", str(empty)]) == 1
    with pytest.raises(BundleValidationError, match="not a directory"):
        validate_bundle(ok / "manifest.json")


def test_rekor_accepts_inclusion_proof_and_rejects_bool_log_index(tmp_path: Path) -> None:
    proof = {
        "logIndex": 1,
        "logId": {"keyId": "abc"},
        "integratedTime": 1,
        "canonicalizedBody": "Zg==",
        "inclusionProof": {"checkpoint": "note"},
    }
    bundle = write_bundle(
        tmp_path,
        signer=SIGNER,
        signature={"rekor": [proof]},
        name="proof",
    )
    validate_bundle(bundle)

    bool_index = dict(proof)
    bool_index["logIndex"] = True
    rejected = write_bundle(
        tmp_path,
        signer=SIGNER,
        signature={"verificationMaterial": {"tlogEntries": [bool_index]}},
        name="bool-index",
    )
    with pytest.raises(BundleValidationError, match="malformed"):
        validate_bundle(rejected)


def test_ci_validate_step_requires_signatures() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    validate_steps = [line for line in workflow.splitlines() if "bakeoff_results.validate" in line]

    assert validate_steps
    assert all("--require-signature" in line for line in validate_steps)

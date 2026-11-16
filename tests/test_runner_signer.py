"""Queue submissions are signed by the runner key that signers.yaml allowlists."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from bakeoff_results.build_index import build_index
from bakeoff_results.queue_signing import generate_keypair, public_key_fingerprint, sign_result
from bakeoff_results.queue_store import upsert_runner, write_submission
from bakeoff_results.validate import BundleValidationError, validate_bundle

RUNNER_ID = "runner-1"
RUN_ID = "run-1"


def _result() -> dict[str, Any]:
    return {
        "run_id": RUN_ID,
        "timestamp": "2026-09-20T00:00:00Z",
        "provenance": {"source_repository": "Rethunk-AI/bakeoff", "source_commit": "abc1234"},
        "models": [{"id": "model-a"}],
    }


def _policy(tmp_path: Path, fingerprints: list[str]) -> Path:
    path = tmp_path / "policy" / "signers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version: bakeoff-results-signers/v1", "runners:"]
    lines.extend(f"  - {fingerprint}" for fingerprint in fingerprints)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _submitted(tmp_path: Path, *, allowlisted: bool) -> tuple[Path, Path, Path]:
    root = tmp_path / "data"
    private_key, public_key = generate_keypair()
    upsert_runner(root, {"runner_id": RUNNER_ID, "public_key": public_key})
    envelope = sign_result(_result(), private_key, RUNNER_ID)
    bundle = write_submission(root, RUNNER_ID, RUN_ID, envelope)
    allowed = [public_key_fingerprint(public_key)] if allowlisted else ["sha256:" + "0" * 64]
    return root, bundle, _policy(tmp_path, allowed)


def test_allowlisted_runner_bundle_validates_and_indexes(tmp_path: Path, monkeypatch) -> None:
    root, bundle, policy = _submitted(tmp_path, allowlisted=True)

    validated = validate_bundle(bundle, policy)
    assert validated.manifest["signer"]["kind"] == "runner"
    assert validated.manifest["signer"]["runner_id"] == RUNNER_ID

    monkeypatch.chdir(policy.parent)
    payload = build_index(root / "submissions", root / "site")
    assert [entry["run_id"] for entry in payload["entries"]] == [RUN_ID]


def test_fingerprint_missing_from_runners_is_rejected(tmp_path: Path) -> None:
    _, bundle, policy = _submitted(tmp_path, allowlisted=False)

    with pytest.raises(BundleValidationError, match="fingerprint is not allowlisted"):
        validate_bundle(bundle, policy)


def test_tampered_bundle_file_is_rejected(tmp_path: Path) -> None:
    _, bundle, policy = _submitted(tmp_path, allowlisted=True)
    result = json.loads((bundle / "result.json").read_text())
    result["models"] = [{"id": "model-b"}]
    (bundle / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    with pytest.raises(BundleValidationError, match="sha256 mismatch"):
        validate_bundle(bundle, policy)


def test_bad_envelope_is_rejected(tmp_path: Path) -> None:
    _, bundle, policy = _submitted(tmp_path, allowlisted=True)
    envelope = json.loads((bundle / "envelope.json").read_text())
    envelope["sig"]["signature"] = "A" * 64
    (bundle / "envelope.json").write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")

    # Re-hash the swapped envelope so only the signature, not the manifest, is wrong.
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["files"]["envelope.json"]["sha256"] = hashlib.sha256(
        (bundle / "envelope.json").read_bytes()
    ).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    with pytest.raises(BundleValidationError, match="envelope failed verification"):
        validate_bundle(bundle, policy)

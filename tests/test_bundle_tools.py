from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bakeoff_results.build_index import build_index
from bakeoff_results.validate import BundleValidationError, validate_bundle


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_bundle(root: Path, *, include_required_result_fields: bool = True) -> Path:
    bundle = root / "publisher" / "run-001"
    bundle.mkdir(parents=True)

    result = {
        "run_id": "run-001",
        "timestamp": "2026-04-26T00:00:00Z",
        "judge": {"mode": "static-fixture"},
        "config": {"hash": "config-sha256"},
    }
    if include_required_result_fields:
        result["provenance"] = {
            "source_repository": "Rethunk-AI/bakeoff",
            "source_commit": "abc1234",
        }
        result["models"] = [{"id": "model-a"}, {"model_id": "model-b"}]

    write_json(bundle / "result.json", result)
    (bundle / "summary.md").write_text("# Summary\n\nFixture result.\n", encoding="utf-8")
    write_json(
        bundle / "signature.sigstore.json",
        {"verificationMaterial": {"transparencyLogEntries": [{"logIndex": 1}]}},
    )

    manifest = {
        "schema_version": "bakeoff-results/v1",
        "bundle": {
            "run_id": "run-001",
            "timestamp": "2026-04-26T00:00:00Z",
        },
        "signer": {
            "identity": "github-actions[bot]",
            "issuer": "https://token.actions.githubusercontent.com",
            "repository": "Rethunk-AI/bakeoff",
            "policy": "bakeoff-results-signers/v1",
        },
        "files": {
            "result.json": {"sha256": digest(bundle / "result.json")},
            "summary.md": {"sha256": digest(bundle / "summary.md")},
            "signature.sigstore.json": {
                "sha256": digest(bundle / "signature.sigstore.json")
            },
        },
    }
    write_json(bundle / "manifest.json", manifest)
    return bundle


class BundleValidationTests(unittest.TestCase):
    def test_valid_bundle_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))

            validated = validate_bundle(bundle)

            self.assertEqual(validated.result["run_id"], "run-001")
            self.assertEqual(validated.manifest["schema_version"], "bakeoff-results/v1")

    def test_tampered_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp))
            (bundle / "summary.md").write_text("tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(BundleValidationError, "sha256 mismatch"):
                validate_bundle(bundle)

    def test_missing_required_result_fields_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp), include_required_result_fields=False)

            with self.assertRaisesRegex(BundleValidationError, "provenance"):
                validate_bundle(bundle)


class IndexBuilderTests(unittest.TestCase):
    def test_index_outputs_json_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bundle(root / "submissions")

            payload = build_index(root / "submissions", root / "site")

            self.assertEqual(len(payload["entries"]), 1)
            entry = payload["entries"][0]
            self.assertEqual(entry["run_id"], "run-001")
            self.assertEqual(entry["model_ids"], ["model-a", "model-b"])
            self.assertEqual(entry["judge_mode"], "static-fixture")
            self.assertEqual(entry["config_hash"], "config-sha256")
            self.assertTrue((root / "site" / "index.json").is_file())
            self.assertIn(
                "Rethunk Bakeoff Results",
                (root / "site" / "index.html").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()

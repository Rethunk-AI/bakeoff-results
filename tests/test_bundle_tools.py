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


def make_bundle(
    root: Path,
    *,
    include_required_result_fields: bool = True,
    extra_result: dict | None = None,
) -> Path:
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
    if extra_result:
        result.update(extra_result)

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


def make_bakeoff_bundle(root: Path) -> Path:
    bundle = root / "publisher" / "test-run"
    bundle.mkdir(parents=True)

    result = {
        "run_id": "test-run",
        "timestamp": "20260427-010000",
        "config": {
            "models": [{"id": "m_a", "gguf": "org/repo/model-Q4_K_M.gguf"}],
            "prompts": [{"id": "plain", "system": "Answer."}],
            "judge": {"enabled": True, "mode": "pairwise_all"},
        },
        "provenance": {
            "config_hash": "abc123",
            "seed": 42,
            "git": {"sha": "deadbee", "branch": "main", "dirty": False},
        },
        "model_metadata": [
            {"id": "m_a", "gguf": "org/repo/model-Q4_K_M.gguf", "repo_id": "org/repo"},
        ],
        "tasks": [{"id": "t1", "domain": "qa", "user_prompt": "Question?"}],
        "records": [
            {
                "task_id": "t1",
                "prompt_id": "plain",
                "model_id": "m_a",
                "text": "Answer",
                "latency_s": 1.0,
                "ttft_s": 0.1,
                "tokens_per_sec": 12.0,
                "energy_wh": None,
                "cost_usd": None,
                "quality_heuristic": 1.0,
                "error": None,
            }
        ],
        "judgements": [],
    }
    write_json(bundle / "result.json", result)
    (bundle / "summary.md").write_text("# Summary\n\nFixture result.\n", encoding="utf-8")
    (bundle / "dashboard.html").write_text("<h1>Fixture</h1>\n", encoding="utf-8")

    manifest = {
        "schema_version": "bakeoff-results/v1",
        "created_at": "2026-04-26T00:00:00Z",
        "run_id": "test-run",
        "timestamp": "20260427-010000",
        "config_hash": "abc123",
        "judge_mode": "pairwise_all",
        "model_ids": ["m_a"],
        "files": {
            "result.json": {"sha256": digest(bundle / "result.json")},
            "summary.md": {"sha256": digest(bundle / "summary.md")},
            "dashboard.html": {"sha256": digest(bundle / "dashboard.html")},
        },
        "signature": {
            "kind": "sigstore-bundle",
            "path": None,
            "signed_file": "result.json",
            "required": False,
        },
    }
    write_json(bundle / "manifest.json", manifest)
    return bundle


def make_post23_bundle(root: Path) -> Path:
    """Fixture for a bakeoff#23-schema bundle: multi-model, mixed status.

    Three models: one complete, one incomplete (timeout), one failed (load_failure).
    run_status = "failed" (worst-of aggregate). Tests the display wiring for
    run_status → state badge, model_scores → per-model detail, and score
    derivation from non-complete models' partial_scores.
    """
    bundle = root / "publisher" / "multi-run"
    bundle.mkdir(parents=True)

    result = {
        "run_id": "multi-run",
        "timestamp": "2026-06-04T06:00:00Z",
        "run_status": "failed",
        "config": {
            "models": [
                {"id": "m_a", "gguf": "org/repo/model-a.gguf"},
                {"id": "m_b", "gguf": "org/repo/model-b.gguf"},
                {"id": "m_c", "gguf": "org/repo/model-c.gguf"},
            ],
            "prompts": [{"id": "plain", "system": "Answer."}],
            "judge": {"enabled": False, "mode": "none"},
        },
        "provenance": {
            "config_hash": "multi-cfg-hash",
            "seed": 42,
            "git": {"sha": "deadbee", "branch": "main", "dirty": False},
        },
        "model_metadata": [
            {"id": "m_a", "gguf": "org/repo/model-a.gguf"},
            {"id": "m_b", "gguf": "org/repo/model-b.gguf"},
            {"id": "m_c", "gguf": "org/repo/model-c.gguf"},
        ],
        "model_scores": [
            {
                "model_id": "m_a",
                "status": "complete",
                "cells_total": 10,
                "cells_attempted": 10,
                "cells_failed": 0,
                "completeness": 1.0,
                "partial_score": 0.8,
                "dominant_failure_code": None,
                "floor_score": 1.0,
                "floor_cells_passed": 3,
                "floor_cells_total": 3,
            },
            {
                "model_id": "m_b",
                "status": "incomplete",
                "cells_total": 10,
                "cells_attempted": 7,
                "cells_failed": 3,
                "completeness": 0.7,
                "partial_score": 0.5,
                "dominant_failure_code": "timeout",
                "floor_score": 0.67,
                "floor_cells_passed": 2,
                "floor_cells_total": 3,
            },
            {
                "model_id": "m_c",
                "status": "failed",
                "cells_total": 10,
                "cells_attempted": 0,
                "cells_failed": 10,
                "completeness": 0.0,
                "partial_score": 0.0,
                "dominant_failure_code": "load_failure",
                "floor_score": None,
                "floor_cells_passed": None,
                "floor_cells_total": None,
            },
        ],
        "tasks": [{"id": "t1", "domain": "qa", "user_prompt": "Question?"}],
        "records": [
            {
                "task_id": "t1",
                "prompt_id": "plain",
                "model_id": "m_a",
                "text": "Answer.",
                "wall_clock_seconds": 1.0,
                "quality_heuristic": 0.8,
                "failure_code": None,
                "failure_detail": None,
                "error": None,
            }
        ],
        "judgements": [],
    }
    write_json(bundle / "result.json", result)
    (bundle / "summary.md").write_text("# Summary\n\nFixture multi-model result.\n", encoding="utf-8")
    (bundle / "dashboard.html").write_text("<h1>Fixture</h1>\n", encoding="utf-8")

    manifest = {
        "schema_version": "bakeoff-results/v1",
        "created_at": "2026-06-04T06:00:00Z",
        "run_id": "multi-run",
        "timestamp": "2026-06-04T06:00:00Z",
        "config_hash": "multi-cfg-hash",
        "judge_mode": "none",
        "model_ids": ["m_a", "m_b", "m_c"],
        "run_status": "failed",
        "model_scores_summary": [
            {"model_id": "m_a", "status": "complete", "partial_score": 0.8,
             "floor_score": 1.0, "dominant_failure_code": None},
            {"model_id": "m_b", "status": "incomplete", "partial_score": 0.5,
             "floor_score": 0.67, "dominant_failure_code": "timeout"},
            {"model_id": "m_c", "status": "failed", "partial_score": 0.0,
             "floor_score": None, "dominant_failure_code": "load_failure"},
        ],
        "files": {
            "result.json": {"sha256": digest(bundle / "result.json")},
            "summary.md": {"sha256": digest(bundle / "summary.md")},
            "dashboard.html": {"sha256": digest(bundle / "dashboard.html")},
        },
        "signature": {
            "kind": "sigstore-bundle",
            "path": None,
            "signed_file": "result.json",
            "required": False,
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

    def test_current_bakeoff_bundle_shape_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bakeoff_bundle(Path(tmp))

            validated = validate_bundle(bundle)

            self.assertEqual(validated.result["run_id"], "test-run")


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
            html = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Rethunk Bakeoff Results", html)
            self.assertIn('id="filter-toggle"', html)
            self.assertNotIn("<tr><tr>", html)

    def test_index_accepts_current_bakeoff_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bakeoff_bundle(root / "submissions")

            payload = build_index(root / "submissions", root / "site")

            entry = payload["entries"][0]
            self.assertEqual(entry["model_ids"], ["m_a"])
            self.assertEqual(entry["judge_mode"], "pairwise_all")
            self.assertEqual(entry["config_hash"], "abc123")

    def test_state_score_and_failure_render(self) -> None:
        # A non-finishing/graded run: state, partial score, and failure reason
        # are extracted and surfaced inline without adding table columns.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bundle(
                root / "submissions",
                extra_result={
                    "state": "Incomplete",  # case-insensitive
                    "partial_score": 0.42,
                    "failure_reason": "timeout after 600s",
                },
            )

            payload = build_index(root / "submissions", root / "site")
            entry = payload["entries"][0]

            self.assertEqual(entry["state"], "incomplete")
            self.assertEqual(entry["score"], "0.42")
            self.assertEqual(entry["failure_reason"], "timeout after 600s")
            # cohort = judge_mode|config_hash
            self.assertEqual(entry["cohort"], "static-fixture|config-sha256")

            html = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("state-incomplete", html)
            self.assertIn("score-badge", html)
            self.assertIn("timeout after 600s", html)
            self.assertIn('id="f-state"', html)
            self.assertNotIn('id="f-cohort"', html)
            # Column count unchanged — no Column Count Mismatch (cf. closed #20)
            self.assertEqual(html.count('data-col-index="'), 13)

    def test_no_state_renders_no_badge(self) -> None:
        # Graceful degradation: bundles without the new fields render cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bundle(root / "submissions")

            payload = build_index(root / "submissions", root / "site")
            entry = payload["entries"][0]

            self.assertIsNone(entry["state"])
            self.assertIsNone(entry["score"])
            self.assertIsNone(entry["failure_reason"])
            html = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("state-badge state-", html)

    def test_bakeoff23_run_status_wired_to_state_badge(self) -> None:
        # bakeoff#23 schema: run_status → state badge (not the legacy `state` field).
        # A multi-model run with mixed statuses: worst-of is "failed".
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_post23_bundle(root / "submissions")

            payload = build_index(root / "submissions", root / "site")
            entry = payload["entries"][0]

            # run_status "failed" → state badge rendered
            self.assertEqual(entry["state"], "failed")
            # Complete model (m_a) does not contribute to score; only non-complete do.
            # Non-complete: m_b (0.5) + m_c (0.0) → mean = 0.25
            self.assertIsNotNone(entry["score"])
            # failure_reason derived from worst model's dominant_failure_code
            # Worst (failed): m_c → "load_failure"
            self.assertEqual(entry["failure_reason"], "load_failure")

            html = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("state-failed", html)
            self.assertIn("score-badge", html)
            # Column count must not change (no new columns)
            self.assertEqual(html.count('data-col-index="'), 13)

    def test_bakeoff23_per_model_detail_in_actions_menu(self) -> None:
        # Per-model score + failure code should appear in the actions-menu detail.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_post23_bundle(root / "submissions")

            payload = build_index(root / "submissions", root / "site")
            entry = payload["entries"][0]

            detail = entry["model_scores_detail"]
            self.assertEqual(len(detail), 3)
            # m_a: complete — present in detail (complete model rendered too)
            m_a = next(d for d in detail if d["model_id"] == "m_a")
            self.assertEqual(m_a["status"], "complete")
            self.assertAlmostEqual(m_a["partial_score"], 0.8)
            self.assertIsNone(m_a["dominant_failure_code"])
            # m_b: incomplete + timeout
            m_b = next(d for d in detail if d["model_id"] == "m_b")
            self.assertEqual(m_b["status"], "incomplete")
            self.assertEqual(m_b["dominant_failure_code"], "timeout")
            # m_c: failed + load_failure
            m_c = next(d for d in detail if d["model_id"] == "m_c")
            self.assertEqual(m_c["status"], "failed")
            self.assertEqual(m_c["dominant_failure_code"], "load_failure")

            html = (root / "site" / "index.html").read_text(encoding="utf-8")
            # Per-model detail rows rendered in actions-menu-info divs
            self.assertIn("timeout", html)
            self.assertIn("load_failure", html)
            self.assertIn("incomplete", html)

    def test_bakeoff23_complete_run_no_state_badge(self) -> None:
        # A fully-complete run (run_status="complete") must not render a state badge.
        # "complete" is intentionally absent from VALID_STATES.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bundle(
                root / "submissions",
                extra_result={
                    "run_status": "complete",
                    "model_scores": [
                        {
                            "model_id": "m_a",
                            "status": "complete",
                            "partial_score": 0.9,
                            "dominant_failure_code": None,
                        }
                    ],
                },
            )

            payload = build_index(root / "submissions", root / "site")
            entry = payload["entries"][0]

            # run_status "complete" → no state badge (not in VALID_STATES)
            self.assertIsNone(entry["state"])
            # No non-complete models → no score badge on a fully-complete run
            self.assertIsNone(entry["score"])

            html = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("state-badge state-", html)
            # score-badge appears in the CSS stylesheet but not as a rendered element
            self.assertNotIn('<span class="score-badge"', html)

    def test_bakeoff23_manifest_fallback_for_model_scores(self) -> None:
        # When result.json has no model_scores, fall back to manifest model_scores_summary.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Use make_bakeoff_bundle as base but the manifest carries model_scores_summary
            # while result.json doesn't — simulates a manifest-only path.
            bundle = root / "submissions" / "publisher" / "fallback-run"
            bundle.mkdir(parents=True)

            result = {
                "run_id": "fallback-run",
                "timestamp": "2026-06-04T07:00:00Z",
                "run_status": "incomplete",
                "config": {
                    "models": [{"id": "m_x", "gguf": "org/repo/model-x.gguf"}],
                    "prompts": [{"id": "plain", "system": "Answer."}],
                    "judge": {"enabled": False},
                },
                "provenance": {
                    "config_hash": "fallback-hash",
                    "seed": 1,
                    "git": {"sha": "abc1234", "branch": "main", "dirty": False},
                },
                "model_metadata": [{"id": "m_x", "gguf": "org/repo/model-x.gguf"}],
                "tasks": [{"id": "t1", "domain": "qa", "user_prompt": "Q?"}],
                "records": [
                    {"task_id": "t1", "prompt_id": "plain", "model_id": "m_x",
                     "text": "A", "wall_clock_seconds": 1.0,
                     "failure_code": "timeout", "failure_detail": "exceeded 60s", "error": "timeout"}
                ],
                "judgements": [],
                # No model_scores key — manifest_fallback path
            }
            write_json(bundle / "result.json", result)
            (bundle / "summary.md").write_text("# Summary\n\nFallback.\n", encoding="utf-8")

            manifest = {
                "schema_version": "bakeoff-results/v1",
                "created_at": "2026-06-04T07:00:00Z",
                "run_id": "fallback-run",
                "timestamp": "2026-06-04T07:00:00Z",
                "config_hash": "fallback-hash",
                "judge_mode": "none",
                "model_ids": ["m_x"],
                "run_status": "incomplete",
                "model_scores_summary": [
                    {"model_id": "m_x", "status": "incomplete",
                     "partial_score": 0.3, "floor_score": None,
                     "dominant_failure_code": "timeout"},
                ],
                "files": {
                    "result.json": {"sha256": digest(bundle / "result.json")},
                    "summary.md": {"sha256": digest(bundle / "summary.md")},
                },
                "signature": {
                    "kind": "sigstore-bundle",
                    "path": None,
                    "signed_file": "result.json",
                    "required": False,
                },
            }
            write_json(bundle / "manifest.json", manifest)

            payload = build_index(root / "submissions", root / "site")
            entry = payload["entries"][0]

            self.assertEqual(entry["state"], "incomplete")
            self.assertIsNotNone(entry["score"])  # derived from manifest fallback
            self.assertEqual(entry["failure_reason"], "timeout")
            # model_scores_detail populated from manifest fallback
            self.assertEqual(len(entry["model_scores_detail"]), 1)
            self.assertEqual(entry["model_scores_detail"][0]["dominant_failure_code"], "timeout")


if __name__ == "__main__":
    unittest.main()

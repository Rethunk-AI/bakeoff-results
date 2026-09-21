from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bakeoff_results.queue_app import QueueSettings, dispatch
from bakeoff_results.queue_server import make_handler
from bakeoff_results.queue_signing import generate_keypair, sign_result
from bakeoff_results.queue_store import (
    add_whitelist_key,
    claim,
    enqueue,
    fail,
    list_pending,
    reap_stale_claims,
    set_runner_status,
    upsert_runner,
)


def _result(run_id: str, model_id: str = "model-a") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "timestamp": "2026-09-20T00:00:00Z",
        "provenance": {
            "source_repository": "Rethunk-AI/bakeoff",
            "source_commit": "abc1234",
        },
        "models": [{"id": model_id}],
    }


class QueueStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["BAKEOFF_RESULTS_DATA_DIR"] = str(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_enqueue_claim_complete_happy(self) -> None:
        job = enqueue(self.root, {"model_id": "qwen", "run_id": "run-1"})
        claimed = claim(self.root, "runner-1", {"vram_mb": 8192})
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertEqual(claimed["queue_id"], job["queue_id"])

    def test_capability_filter_skips_under_vram(self) -> None:
        enqueue(self.root, {"model_id": "big", "min_vram_mb": 20000})
        self.assertIsNone(claim(self.root, "runner-1", {"vram_mb": 8192}))
        claimed = claim(self.root, "runner-1", {"vram_mb": 24576})
        self.assertIsNotNone(claimed)

    def test_concurrent_claim_has_one_winner(self) -> None:
        enqueue(self.root, {"model_id": "only"})
        winners: list[str] = []

        def worker(name: str) -> None:
            item = claim(self.root, name)
            if item is not None:
                winners.append(name)

        threads = [threading.Thread(target=worker, args=(f"r{i}",)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(winners), 1)
        pending = list_pending(self.root)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "CLAIMED")

    def test_reap_stale_returns_job_to_pending(self) -> None:
        enqueue(self.root, {"model_id": "stale"})
        claimed = claim(self.root, "runner-1")
        assert claimed is not None
        claimed["claimed_at"] = "2000-01-01T00:00:00Z"
        claimed["last_heartbeat"] = "2000-01-01T00:00:00Z"
        path = self.root / "run_queue" / "pending" / f"{claimed['queue_id']}.json"
        path.write_text(json.dumps(claimed, indent=2, sort_keys=True) + "\n")
        reaped = reap_stale_claims(self.root, timeout_seconds=60)
        self.assertEqual(reaped, [claimed["queue_id"]])
        self.assertEqual(list_pending(self.root)[0]["status"], "PENDING")

    def test_pause_keeps_current_claim(self) -> None:
        upsert_runner(
            self.root,
            {"runner_id": "runner-1", "status": "ACTIVE", "current_claim": "job-1"},
        )
        paused = set_runner_status(self.root, "runner-1", "PAUSED")
        self.assertEqual(paused["status"], "PAUSED")
        self.assertEqual(paused["current_claim"], "job-1")

    def test_fail_retries_then_marks_failed(self) -> None:
        enqueue(self.root, {"model_id": "qwen", "max_attempts": 1})
        claimed = claim(self.root, "runner-1")
        assert claimed is not None
        retried = fail(self.root, claimed["queue_id"], "oom", "runner-1")
        self.assertEqual(retried["status"], "PENDING")
        self.assertEqual(retried["error_detail"], "oom")
        dead = fail(self.root, claimed["queue_id"], "oom again")
        self.assertEqual(dead["status"], "FAILED")


class QueueApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["BAKEOFF_RESULTS_DATA_DIR"] = str(self.root)
        self.private, self.public = generate_keypair()
        add_whitelist_key(self.root, self.public)
        self.settings = QueueSettings(
            data_dir=self.root,
            secret=b"test-secret",
            admin_token="admin-token",
            heartbeat_ttl_s=120,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _dispatch(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        admin: bool = False,
    ) -> tuple[int, dict[str, Any] | None]:
        headers: dict[str, str] = {}
        if admin:
            headers["Authorization"] = "Bearer admin-token"
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(payload).encode() if payload is not None else b""
        response = dispatch(self.settings, method, path, headers, body)
        if response.status == 204:
            return 204, None
        data = json.loads(response.body.decode()) if response.body else None
        return response.status, data

    def test_unregistered_key_is_rejected(self) -> None:
        _, other_pub = generate_keypair()
        status, data = self._dispatch(
            "POST",
            "/api/runners/register",
            {"public_key": other_pub, "hostname": "box"},
        )
        self.assertEqual(status, 403)
        assert data is not None
        self.assertIn("whitelist", data["error"])

    def test_register_claim_heartbeat_submit(self) -> None:
        status, registered = self._dispatch(
            "POST",
            "/api/runners/register",
            {
                "public_key": self.public,
                "hostname": "box",
                "capabilities": {"vram_mb": 32768, "quantization": ["q4_k_m"]},
            },
        )
        self.assertEqual(status, 200)
        assert registered is not None
        token = registered["token"]
        runner_id = registered["runner"]["runner_id"]

        enqueue(self.root, {"model_id": "qwen3.5-9b", "run_id": "job-1", "queue_id": "job-1"})
        status, claimed = self._dispatch("POST", "/api/queue/claim", {}, token=token)
        self.assertEqual(status, 200)
        assert claimed is not None
        self.assertEqual(claimed["job"]["model_id"], "qwen3.5-9b")

        status, _ = self._dispatch("POST", "/api/queue/job-1/heartbeat", {}, token=token)
        self.assertEqual(status, 200)

        envelope = sign_result(_result("job-1", "qwen3.5-9b"), self.private, runner_id)
        status, submitted = self._dispatch(
            "POST",
            "/api/queue/job-1/submit",
            envelope,
            token=token,
        )
        self.assertEqual(status, 200)
        assert submitted is not None
        self.assertEqual(submitted["job"]["status"], "COMPLETE")

    def test_empty_claim_is_204(self) -> None:
        status, registered = self._dispatch(
            "POST",
            "/api/runners/register",
            {"public_key": self.public},
        )
        assert registered is not None
        status, body = self._dispatch(
            "POST",
            "/api/queue/claim",
            {},
            token=registered["token"],
        )
        self.assertEqual(status, 204)
        self.assertIsNone(body)

    def test_paused_runner_cannot_claim(self) -> None:
        status, registered = self._dispatch(
            "POST",
            "/api/runners/register",
            {"public_key": self.public, "hostname": "box"},
        )
        self.assertEqual(status, 200)
        assert registered is not None
        runner_id = registered["runner"]["runner_id"]
        enqueue(self.root, {"model_id": "qwen", "run_id": "paused-job", "queue_id": "paused-job"})
        status, _ = self._dispatch(
            "POST",
            f"/api/admin/runners/{runner_id}/status",
            {"status": "PAUSED"},
            admin=True,
        )
        self.assertEqual(status, 200)
        status, data = self._dispatch(
            "POST",
            "/api/queue/claim",
            {},
            token=registered["token"],
        )
        self.assertEqual(status, 403)
        assert data is not None
        self.assertIn("paused", data["error"])
        status, _ = self._dispatch(
            "POST",
            f"/api/admin/runners/{runner_id}/status",
            {"status": "IDLE"},
            admin=True,
        )
        status, claimed = self._dispatch(
            "POST",
            "/api/queue/claim",
            {},
            token=registered["token"],
        )
        self.assertEqual(status, 200)
        assert claimed is not None
        self.assertEqual(claimed["job"]["queue_id"], "paused-job")

    def test_fail_reports_error_and_requeues(self) -> None:
        status, registered = self._dispatch(
            "POST",
            "/api/runners/register",
            {"public_key": self.public},
        )
        assert registered is not None
        enqueue(self.root, {"model_id": "qwen", "run_id": "fail-job", "queue_id": "fail-job"})
        self._dispatch("POST", "/api/queue/claim", {}, token=registered["token"])
        status, data = self._dispatch(
            "POST",
            "/api/queue/fail-job/fail",
            {"error": "cuda oom"},
            token=registered["token"],
        )
        self.assertEqual(status, 200)
        assert data is not None
        self.assertEqual(data["job"]["status"], "PENDING")
        self.assertEqual(data["job"]["error_detail"], "cuda oom")

    def test_admin_add_and_remove_key(self) -> None:
        _, extra = generate_keypair()
        status, data = self._dispatch(
            "POST",
            "/api/admin/keys",
            {"action": "add", "public_key": extra},
            admin=True,
        )
        self.assertEqual(status, 200)
        assert data is not None
        self.assertIn(extra, data["public_keys"])
        status, data = self._dispatch(
            "POST",
            "/api/admin/keys",
            {"action": "remove", "public_key": extra},
            admin=True,
        )
        self.assertEqual(status, 200)
        assert data is not None
        self.assertNotIn(extra, data["public_keys"])

    def test_bad_signature_rejected(self) -> None:
        status, registered = self._dispatch(
            "POST",
            "/api/runners/register",
            {"public_key": self.public},
        )
        assert registered is not None
        enqueue(self.root, {"model_id": "qwen", "run_id": "job-2", "queue_id": "job-2"})
        self._dispatch("POST", "/api/queue/claim", {}, token=registered["token"])
        other_priv, _ = generate_keypair()
        envelope = sign_result(_result("job-2"), other_priv, registered["runner"]["runner_id"])
        status, data = self._dispatch(
            "POST",
            "/api/queue/job-2/submit",
            envelope,
            token=registered["token"],
        )
        self.assertEqual(status, 400)
        assert data is not None
        self.assertIn("signature", data["error"].lower())

    def test_dashboard_html(self) -> None:
        response = dispatch(self.settings, "GET", "/runners", {}, b"")
        self.assertEqual(response.status, 200)
        self.assertIn(b"Bakeoff runners", response.body)
        self.assertIn(b"data-status='PAUSED'", response.body)
        self.assertIn(b"error_detail", response.body)

    def test_http_server_health(self) -> None:
        handler = make_handler(self.settings)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host = str(httpd.server_name)
            port = int(httpd.server_port)
            with urlopen(f"http://{host}:{port}/health", timeout=2) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload, {"ok": True})
            req = Request(
                f"http://{host}:{port}/api/runners",
                headers={"Authorization": "Bearer admin-token"},
            )
            with urlopen(req, timeout=2) as response:
                roster = json.loads(response.read())
            self.assertEqual(roster["runners"], [])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()

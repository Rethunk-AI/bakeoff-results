from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bakeoff_results import queue_store
from bakeoff_results.build_index import build_index
from bakeoff_results.queue_app import QueueSettings, dispatch
from bakeoff_results.queue_auth import load_or_create_admin_token, load_or_create_secret
from bakeoff_results.queue_server import make_handler
from bakeoff_results.queue_signing import generate_keypair, public_key_fingerprint, sign_result
from bakeoff_results.queue_store import (
    QueueStoreError,
    add_whitelist_key,
    claim,
    complete,
    enqueue,
    fail,
    get_runner,
    heartbeat,
    list_completed,
    list_pending,
    reap_stale_claims,
    set_runner_status,
    upsert_runner,
)
from bakeoff_results.validate import discover_bundles


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

    def test_string_quantization_cap_is_exact_match(self) -> None:
        caps = {"quantization": "q4_k_m"}
        self.assertFalse(queue_store._job_eligible({"quantization": "q4"}, caps))
        self.assertFalse(queue_store._job_eligible({"quantization": "k_m"}, caps))
        self.assertTrue(queue_store._job_eligible({"quantization": "q4_k_m"}, caps))

    def test_list_quantization_cap_is_membership(self) -> None:
        caps = {"quantization": ["q4_k_m", "fp16"]}
        self.assertTrue(queue_store._job_eligible({"quantization": "q4_k_m"}, caps))
        self.assertTrue(queue_store._job_eligible({"quantization": "fp16"}, caps))
        self.assertFalse(queue_store._job_eligible({"quantization": "q4"}, caps))

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

    def _registered(self) -> tuple[str, str]:
        _, registered = self._dispatch(
            "POST", "/api/runners/register", {"public_key": self.public, "hostname": "box"}
        )
        assert registered is not None
        return registered["token"], registered["runner"]["runner_id"]

    def _runner_status(self, runner_id: str) -> str:
        _, data = self._dispatch("GET", "/api/runners", admin=True)
        assert data is not None
        return next(r["status"] for r in data["runners"] if r["runner_id"] == runner_id)

    def test_revoked_runner_cannot_submit_or_revive(self) -> None:
        token, runner_id = self._registered()
        enqueue(self.root, {"model_id": "m", "run_id": "job-r", "queue_id": "job-r"})
        self._dispatch("POST", "/api/queue/claim", {}, token=token)
        self._dispatch(
            "POST", "/api/admin/keys", {"action": "remove", "public_key": self.public}, admin=True
        )
        envelope = sign_result(_result("job-r"), self.private, runner_id)
        for path, payload in (
            ("/api/queue/job-r/submit", envelope),
            ("/api/queue/job-r/heartbeat", {}),
            ("/api/queue/job-r/fail", {"error": "x"}),
        ):
            status, _ = self._dispatch("POST", path, payload, token=token)
            self.assertEqual(status, 401, path)
        self.assertEqual(self._runner_status(runner_id), "DEAD")
        self.assertFalse((self.root / "submissions").exists())

    def test_paused_runner_stays_paused_through_heartbeat_and_reregister(self) -> None:
        token, runner_id = self._registered()
        enqueue(self.root, {"model_id": "m", "run_id": "job-p", "queue_id": "job-p"})
        self._dispatch("POST", "/api/queue/claim", {}, token=token)
        self._dispatch(
            "POST", f"/api/admin/runners/{runner_id}/status", {"status": "PAUSED"}, admin=True
        )
        self._dispatch("POST", "/api/queue/job-p/heartbeat", {}, token=token)
        self.assertEqual(self._runner_status(runner_id), "PAUSED")
        self._registered()
        self.assertEqual(self._runner_status(runner_id), "PAUSED")

    def test_submit_for_unclaimed_job_writes_nothing(self) -> None:
        token, runner_id = self._registered()
        enqueue(self.root, {"model_id": "m", "run_id": "job-x", "queue_id": "job-x"})
        envelope = sign_result(_result("job-x"), self.private, runner_id)
        status, _ = self._dispatch("POST", "/api/queue/job-x/submit", envelope, token=token)
        self.assertEqual(status, 400)
        self.assertFalse((self.root / "submissions").exists())

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
        thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
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

    def test_register_cannot_hijack_another_runner_id(self) -> None:
        _, registered = self._dispatch(
            "POST", "/api/runners/register", {"public_key": self.public, "hostname": "victim"}
        )
        assert registered is not None
        runner_id = registered["runner"]["runner_id"]
        attacker_private, attacker_public = generate_keypair()
        add_whitelist_key(self.root, attacker_public)
        enqueue(self.root, {"model_id": "m", "run_id": "job-v", "queue_id": "job-v"})
        self._dispatch("POST", "/api/queue/claim", {}, token=registered["token"])

        status, data = self._dispatch(
            "POST",
            "/api/runners/register",
            {"public_key": attacker_public, "runner_id": runner_id},
        )
        self.assertEqual(status, 403)
        assert data is not None
        self.assertIn("public key", data["error"])
        stored = get_runner(self.root, runner_id)
        assert stored is not None
        self.assertEqual(stored["public_key"], self.public)

        # The attacker's own key still registers under its own id, but holds no claim.
        status, own = self._dispatch(
            "POST", "/api/runners/register", {"public_key": attacker_public}
        )
        self.assertEqual(status, 200)
        assert own is not None
        envelope = sign_result(_result("job-v"), attacker_private, own["runner"]["runner_id"])
        status, _ = self._dispatch("POST", "/api/queue/job-v/submit", envelope, token=own["token"])
        self.assertEqual(status, 400)
        self.assertFalse((self.root / "submissions").exists())

    def test_reap_keeps_paused_and_reregister_never_clears_status(self) -> None:
        token, runner_id = self._registered()
        enqueue(self.root, {"model_id": "m", "run_id": "job-s", "queue_id": "job-s"})
        self._dispatch("POST", "/api/queue/claim", {}, token=token)
        self._dispatch(
            "POST", f"/api/admin/runners/{runner_id}/status", {"status": "PAUSED"}, admin=True
        )
        job_path = self.root / "run_queue" / "pending" / "job-s.json"
        job = json.loads(job_path.read_text())
        job["claimed_at"] = "2000-01-01T00:00:00Z"
        job["last_heartbeat"] = "2000-01-01T00:00:00Z"
        job_path.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n")
        self.assertEqual(reap_stale_claims(self.root, 60), ["job-s"])
        self.assertEqual(self._runner_status(runner_id), "PAUSED")

        self._registered()
        self.assertEqual(self._runner_status(runner_id), "PAUSED")
        enqueue(self.root, {"model_id": "m", "run_id": "job-t", "queue_id": "job-t"})
        status, _ = self._dispatch("POST", "/api/queue/claim", {}, token=token)
        self.assertEqual(status, 403)

        set_runner_status(self.root, runner_id, "DEAD")
        status, registered = self._dispatch(
            "POST", "/api/runners/register", {"public_key": self.public}
        )
        self.assertEqual(status, 200)
        assert registered is not None
        self.assertEqual(self._runner_status(runner_id), "DEAD")
        status, _ = self._dispatch("POST", "/api/queue/claim", {}, token=registered["token"])
        self.assertEqual(status, 403)

    def test_submit_rejects_foreign_run_id_and_never_overwrites(self) -> None:
        token, runner_id = self._registered()
        enqueue(self.root, {"model_id": "m", "run_id": "run-a", "queue_id": "job-a"})
        enqueue(self.root, {"model_id": "m", "run_id": "run-b", "queue_id": "job-b"})

        self.assertEqual(self._dispatch("POST", "/api/queue/claim", {}, token=token)[0], 200)
        envelope = sign_result(_result("run-a"), self.private, runner_id)
        self.assertEqual(
            self._dispatch("POST", "/api/queue/job-a/submit", envelope, token=token)[0], 200
        )

        # job-b is claimed but the envelope names run-a: the replay is refused.
        self.assertEqual(self._dispatch("POST", "/api/queue/claim", {}, token=token)[0], 200)
        status, data = self._dispatch("POST", "/api/queue/job-b/submit", envelope, token=token)
        self.assertEqual(status, 400)
        assert data is not None
        self.assertIn("run_id", data["error"])
        pending = {job["queue_id"]: job["status"] for job in list_pending(self.root)}
        self.assertEqual(pending, {"job-b": "CLAIMED"})
        self.assertEqual(
            sorted(p.name for p in (self.root / "submissions" / runner_id).iterdir()), ["run-a"]
        )

    def test_submit_never_replaces_a_stored_run(self) -> None:
        token, runner_id = self._registered()
        enqueue(self.root, {"model_id": "m", "run_id": "same-run", "queue_id": "job-1"})
        enqueue(self.root, {"model_id": "m", "run_id": "same-run", "queue_id": "job-2"})
        self.assertEqual(self._dispatch("POST", "/api/queue/claim", {}, token=token)[0], 200)
        first = _result("same-run")
        first["partial_score"] = 0.1
        envelope = sign_result(first, self.private, runner_id)
        self.assertEqual(
            self._dispatch("POST", "/api/queue/job-1/submit", envelope, token=token)[0], 200
        )

        self.assertEqual(self._dispatch("POST", "/api/queue/claim", {}, token=token)[0], 200)
        second = _result("same-run")
        second["partial_score"] = 0.99
        replay = sign_result(second, self.private, runner_id)
        status, _ = self._dispatch("POST", "/api/queue/job-2/submit", replay, token=token)
        self.assertEqual(status, 400)
        stored = json.loads(
            (self.root / "submissions" / runner_id / "same-run" / "result.json").read_text()
        )
        self.assertEqual(stored["partial_score"], 0.1)

    def test_duplicate_enqueue_is_refused(self) -> None:
        enqueue(self.root, {"model_id": "model-A", "queue_id": "dup"})
        with self.assertRaisesRegex(QueueStoreError, "duplicate"):
            enqueue(self.root, {"model_id": "model-B", "queue_id": "dup"})
        self.assertEqual([job["model_id"] for job in list_pending(self.root)], ["model-A"])
        claimed = claim(self.root, "runner-1")
        assert claimed is not None
        self.assertEqual(claimed["model_id"], "model-A")

    def test_late_heartbeat_cannot_undo_complete(self) -> None:
        enqueue(self.root, {"model_id": "m", "run_id": "job-h", "queue_id": "job-h"})
        claimed = claim(self.root, "runner-1")
        assert claimed is not None
        pending_path = self.root / "run_queue" / "pending" / "job-h.json"
        in_write = threading.Event()
        release = threading.Event()
        real_write = queue_store._atomic_write

        def stalling_write(root: Path, path: Path, data: dict[str, Any]) -> None:
            if Path(path) == pending_path:
                in_write.set()
                self.assertTrue(release.wait(5))
            real_write(root, path, data)

        errors: list[BaseException] = []

        def run(fn) -> None:
            try:
                fn()
            except QueueStoreError:
                pass
            except Exception as exc:  # a thread's failure must fail the test
                errors.append(exc)

        with mock.patch.object(queue_store, "_atomic_write", stalling_write):
            beat = threading.Thread(
                target=run, args=(lambda: heartbeat(self.root, "job-h", "runner-1"),)
            )
            beat.start()
            self.assertTrue(in_write.wait(5))
            done = threading.Thread(
                target=run, args=(lambda: complete(self.root, "job-h", "runner-1"),)
            )
            done.start()
            # complete() must not finish while the heartbeat still holds the job lock.
            done.join(0.5)
            release.set()
            beat.join(10)
            done.join(10)

        self.assertEqual(errors, [])
        self.assertEqual([job["status"] for job in list_completed(self.root)], ["COMPLETE"])
        self.assertEqual(list_pending(self.root), [])

    def test_claim_ignores_self_declared_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, public_key = generate_keypair()
            add_whitelist_key(root, public_key)
            settings = QueueSettings(
                data_dir=root, secret=b"s", admin_token="a", heartbeat_ttl_s=120
            )
            body = json.dumps({"public_key": public_key, "capabilities": {"vram_mb": 4096}})
            response = dispatch(settings, "POST", "/api/runners/register", {}, body.encode())
            headers = {"Authorization": f"Bearer {json.loads(response.body)['token']}"}
            enqueue(
                root,
                {"model_id": "huge", "run_id": "huge", "min_vram_mb": 80000, "queue_id": "huge"},
            )
            payload = json.dumps({"capabilities": {"vram_mb": 1000000}}).encode()
            self.assertEqual(
                dispatch(settings, "POST", "/api/queue/claim", headers, payload).status, 204
            )
            self.assertEqual(
                dispatch(settings, "POST", "/api/queue/claim", headers, b"{}").status, 204
            )
            still_pending = claim(root, "runner-2", {"vram_mb": 1000000})
            assert still_pending is not None
            self.assertEqual(still_pending["model_id"], "huge")

    def test_write_submission_is_discoverable(self) -> None:
        private_key, public_key = generate_keypair()
        add_whitelist_key(self.root, public_key)
        settings = QueueSettings(
            data_dir=self.root, secret=b"s", admin_token="a", heartbeat_ttl_s=120
        )
        body = json.dumps({"public_key": public_key}).encode()
        registered = json.loads(dispatch(settings, "POST", "/api/runners/register", {}, body).body)
        runner_id = registered["runner"]["runner_id"]
        headers = {"Authorization": f"Bearer {registered['token']}"}
        enqueue(self.root, {"model_id": "m", "run_id": "queued", "queue_id": "queued"})
        dispatch(settings, "POST", "/api/queue/claim", headers, b"{}")
        envelope = sign_result(_result("queued"), private_key, runner_id)
        payload = json.dumps(envelope).encode()
        self.assertEqual(
            dispatch(settings, "POST", "/api/queue/queued/submit", headers, payload).status, 200
        )

        bundle = self.root / "submissions" / runner_id / "queued"
        self.assertEqual(
            sorted(path.name for path in bundle.iterdir()),
            ["envelope.json", "manifest.json", "result.json", "summary.md"],
        )
        self.assertEqual(discover_bundles([self.root / "submissions"]), [bundle])

        # A runner-signed bundle reaches the index only through an allowlisted fingerprint.
        policy = Path(self.tmp.name) / "policy" / "signers.yaml"
        policy.parent.mkdir()
        policy.write_text(
            "schema_version: bakeoff-results-signers/v1\n"
            "runners:\n"
            f"  - {public_key_fingerprint(public_key)}\n"
        )
        previous = os.getcwd()
        os.chdir(policy.parent)
        try:
            indexed = build_index(self.root / "submissions", Path(self.tmp.name) / "site")
        finally:
            os.chdir(previous)
        self.assertEqual([entry["run_id"] for entry in indexed["entries"]], ["queued"])


class QueueAuthSecretTests(unittest.TestCase):
    def test_secrets_are_created_private_from_the_first_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created: dict[str, int] = {}
            real_open = os.open
            watched = {"queue_secret", "admin_token"}

            def spying_open(path, flags, mode=0o777, *args, **kwargs):
                fd = real_open(path, flags, mode, *args, **kwargs)
                name = Path(path).name
                if name in watched and flags & os.O_CREAT:
                    created[name] = stat.S_IMODE(os.fstat(fd).st_mode)
                return fd

            env = {
                key: value
                for key, value in os.environ.items()
                if key not in {"BAKEOFF_QUEUE_SECRET", "BAKEOFF_QUEUE_ADMIN_TOKEN"}
            }
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("os.open", spying_open),
            ):
                load_or_create_secret(root)
                load_or_create_admin_token(root)

            # Both files must be born 0600: a chmod afterwards leaves a readable window.
            self.assertEqual(created, {"queue_secret": 0o600, "admin_token": 0o600})
            for name in ("queue_secret", "admin_token"):
                self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o600, name)


if __name__ == "__main__":
    unittest.main()

"""Serve the distributed-worker HTTP queue API.

Operator commands::

    PYTHONPATH=src python -m bakeoff_results.queue_server serve
    PYTHONPATH=src python -m bakeoff_results.queue_server enqueue --model qwen3.5-9b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from bakeoff_results.queue_app import QueueSettings, dispatch
from bakeoff_results.queue_auth import load_or_create_admin_token, load_or_create_secret
from bakeoff_results.queue_store import data_dir, enqueue


def build_settings(root: Path | None = None) -> QueueSettings:
    base = root if root is not None else data_dir()
    ttl = int(os.environ.get("BAKEOFF_QUEUE_HEARTBEAT_TTL_S", "120"))
    return QueueSettings(
        data_dir=base,
        secret=load_or_create_secret(base),
        admin_token=load_or_create_admin_token(base),
        heartbeat_ttl_s=ttl,
    )


def make_handler(settings: QueueSettings) -> type[BaseHTTPRequestHandler]:
    class QueueHandler(BaseHTTPRequestHandler):
        def _headers(self) -> dict[str, str]:
            return {key: value for key, value in self.headers.items()}

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or 0)
            return self.rfile.read(length) if length else b""

        def _dispatch(self, method: str) -> None:
            response = dispatch(settings, method, self.path, self._headers(), self._body())
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD" and response.status != 204:
                self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_HEAD(self) -> None:
            self._dispatch("GET")

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    return QueueHandler


def serve(host: str, port: int, settings: QueueSettings) -> None:
    handler = make_handler(settings)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(
        f"bakeoff queue listening on http://{httpd.server_name}:{httpd.server_port}",
        file=sys.stderr,
    )
    print("admin dashboard: /runners", file=sys.stderr)
    print("set BAKEOFF_QUEUE_ADMIN_TOKEN to override the generated admin token", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Run the queue HTTP server")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument("--data-dir", type=Path)

    enqueue_p = sub.add_parser("enqueue", help="Admin: add a model job to the queue")
    enqueue_p.add_argument("--model", required=True)
    enqueue_p.add_argument("--run-id")
    enqueue_p.add_argument("--priority", type=int, default=100)
    enqueue_p.add_argument("--min-vram-mb", type=int)
    enqueue_p.add_argument("--quantization")
    enqueue_p.add_argument("--data-dir", type=Path)

    args = parser.parse_args(argv)
    root = args.data_dir.resolve() if args.data_dir else data_dir()
    os.environ["BAKEOFF_RESULTS_DATA_DIR"] = str(root)

    if args.command == "serve":
        settings = build_settings(root)
        serve(args.host, args.port, settings)
        return 0

    item: dict[str, Any] = {
        "model_id": args.model,
        "priority": args.priority,
    }
    if args.run_id:
        item["run_id"] = args.run_id
        item["queue_id"] = args.run_id
    if args.min_vram_mb is not None:
        item["min_vram_mb"] = args.min_vram_mb
    if args.quantization:
        item["quantization"] = args.quantization
    job = enqueue(root, item)
    print(json.dumps(job, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

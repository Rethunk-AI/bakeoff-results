# Humans — bakeoff-results

## Requirements

Python 3.12+. Validator and index builder are stdlib-only — set `PYTHONPATH=src` for CLI commands. The optional queue server also needs `cryptography` (`uv sync`).

## Validate submissions

```sh
# scan all bundles
PYTHONPATH=src python -m bakeoff_results.validate --scan --allow-empty submissions
# one bundle
PYTHONPATH=src python -m bakeoff_results.validate submissions/<publisher>/<run-id>
# require Sigstore signatures
PYTHONPATH=src python -m bakeoff_results.validate --scan --require-signature submissions
```

## Build index

```sh
PYTHONPATH=src python -m bakeoff_results.build_index --submissions submissions --site site
```

Outputs `site/index.json` and `site/index.html`.

## Distributed worker queue

Optional HTTP API for `Rethunk-AI/bakeoff` workers (`#31`). Data lives under
`BAKEOFF_RESULTS_DATA_DIR` (default `~/.local/share/bakeoff-results`).

```sh
uv sync
export BAKEOFF_QUEUE_ADMIN_TOKEN=...   # optional; generated into the data dir
PYTHONPATH=src uv run python -m bakeoff_results.queue_server serve --host 127.0.0.1 --port 8765
PYTHONPATH=src uv run python -m bakeoff_results.queue_server enqueue --model qwen3.5-9b
```

Approve a runner's Ed25519 public key (admin bearer token), then the worker can
`POST /api/runners/register`. Dashboard: <http://127.0.0.1:8765/runners>

| Method | Path | Who |
| --- | --- | --- |
| POST | `/api/runners/register` | whitelisted public key |
| POST | `/api/queue/claim` | runner token |
| POST | `/api/queue/<job_id>/heartbeat` | runner token |
| POST | `/api/queue/<job_id>/submit` | runner token (signed envelope) |
| POST | `/api/queue/<job_id>/fail` | runner token (`{"error": "..."}`) |
| POST | `/api/admin/keys` | admin token |
| GET | `/api/runners`, `/api/queue` | admin token |

Stale claims return to `PENDING` after `BAKEOFF_QUEUE_HEARTBEAT_TTL_S` (default 120).
Pause on `/runners` sets `PAUSED` and blocks new claims; the current job stays
with the runner until it submits, fails, or the heartbeat TTL fires. `POST fail`
retries with backoff until `max_attempts`, then marks the job `FAILED`.

## Test

```sh
uv sync
python -m compileall src tests
PYTHONPATH=src uv run python -m unittest discover -s tests
```

## Verify published site

```sh
gh attestation verify site/index.json --repo Rethunk-AI/bakeoff-results
gh attestation verify site/index.html --repo Rethunk-AI/bakeoff-results
```

## Bundle layout

`submissions/<publisher>/<run-id>/`: `result.json`, `manifest.json` (`bakeoff-results/v1`), and `summary.md` are required; `dashboard.html` and `signature.sigstore.json` are optional. `result.json` needs `run_id`, `timestamp`, `provenance`, and `model_ids` or `models`.

## Uninstall

Validator and index builder need no install. If you `uv sync` for the queue server, delete `.venv` and the clone.

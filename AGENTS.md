# Agents — bakeoff-results

## File layout

```
submissions/<publisher>/<run-id>/   staged bundles
site/                               generated index.json + index.html
src/bakeoff_results/
  validate.py                       bundle validator
  build_index.py                    index generator
  queue_store.py                    disk-backed runner roster and job queue
  queue_auth.py                     HMAC runner tokens and admin bearer
  queue_signing.py                  Ed25519 envelope verify/sign
  queue_app.py                      HTTP routing for the worker API
  queue_server.py                   CLI: serve / enqueue
  queue_dashboard.py                admin HTML for /runners
tests/test_bundle_tools.py
tests/test_queue_api.py
signers.yaml                        signer allowlist (bakeoff-results-signers/v1)
signers.example.yaml
GOVERNANCE.md
.github/workflows/ci.yml
```

## Stack

Python 3.12+. Validator and index builder stay stdlib-only. The queue server adds `cryptography` for Ed25519 envelopes. Schemas: `bakeoff-results/v1` (bundle manifests), `bakeoff-results-signers/v1` (signers.yaml).

## CI

**verify** (every push/PR): compile, unit tests, `validate --scan --allow-empty`, `build_index`.

**publish** (`main` only, after verify, `github-pages` env): validate, build_index, attest `site/`, deploy Pages.

## Invariants

- Manifest SHA256 entries must match file contents — never edit bundle files after manifest is written.
- `signers.yaml` is live policy; validator does not read it yet.
- `site/` is generated — rebuild via `build_index.py`, do not hand-edit.
- Claim is rename-as-mutex on JSON files under `BAKEOFF_RESULTS_DATA_DIR`; do not add a SQL runtime to this package.
- Queue submit verifies the bakeoff Ed25519 envelope against the registered public key, then `validate_result`.
- Registration is gated by the public-key whitelist. OAuth is out of scope.
- `PAUSED` runners must not claim; `IDLE` runners may. Pause does not drop an in-flight claim.

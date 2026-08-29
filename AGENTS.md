# Agents — bakeoff-results

## File layout

```
submissions/<publisher>/<run-id>/   staged bundles
site/                               generated index.json + index.html
src/bakeoff_results/
  validate.py                       bundle validator
  build_index.py                    index generator
tests/test_bundle_tools.py
signers.yaml                        signer allowlist (bakeoff-results-signers/v1)
signers.example.yaml
GOVERNANCE.md
.github/workflows/ci.yml
```

## Stack

Python 3.12+, stdlib only. Schemas: `bakeoff-results/v1` (bundle manifests), `bakeoff-results-signers/v1` (signers.yaml).

## CI

**verify** (every push/PR): compile, unit tests, `validate --scan --allow-empty`, `build_index`.

**publish** (`main` only, after verify, `github-pages` env): validate, build_index, attest `site/`, deploy Pages.

## Invariants

- Manifest SHA256 entries must match file contents — never edit bundle files after manifest is written.
- `signers.yaml` is live policy; validator does not read it yet.
- `site/` is generated — rebuild via `build_index.py`, do not hand-edit.
- `publish` requires `github-pages` environment with a required reviewer.

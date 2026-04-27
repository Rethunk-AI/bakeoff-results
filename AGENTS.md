# Agents — bakeoff-results

LLM/developer onboarding for the bakeoff-results repository.

## File layout

```
submissions/              staged bundles — <publisher>/<run-id>/
site/                     generated static artifacts
  index.json              machine-readable leaderboard
  index.html              human-readable leaderboard
src/bakeoff_results/
  validate.py             bundle validator (structural + integrity only)
  build_index.py          static index generator
  __init__.py
tests/
  test_bundle_tools.py    unit tests for validator and index builder
signers.yaml              approved signer allowlist (bakeoff-results-signers/v1)
signers.example.yaml      reference shape for the allowlist schema
GOVERNANCE.md             result states, moderation policy, signer policy narrative
.github/workflows/ci.yml  CI: verify + publish jobs
```

## Schema

Two schema versions are in use:

- **`bakeoff-results/v1`** — `manifest.json` inside every bundle. Records file hashes, signer metadata, and run identity.
- **`bakeoff-results-signers/v1`** — `signers.yaml`. Lists trusted OIDC subjects and identities allowed to submit bundles.

## Submission lifecycle

```
submissions/<publisher>/<run-id>/
  ↓  validate.py: schema check + SHA256 integrity + signer metadata structure
  ↓  build_index.py: extracts run_id, timestamp, signer, models, judge_mode, config_hash
  ↓  site/index.json + site/index.html rebuilt
  ↓  CI publish job: signature gate → attest site/ → deploy to GitHub Pages
```

Validation is structural only. Full Sigstore/Rekor verification of `signature.sigstore.json` is deferred to a future CI step using the Sigstore tooling once the submitting workflow is stable.

## CI jobs

**`verify`** — runs on every push and PR:
- Python compile check
- Unit tests
- `validate --scan --allow-empty submissions` (unsigned bundles accepted)
- `build_index` (rebuilds `site/`)

**`publish`** — runs on `main` pushes only, after `verify`, under the protected `github-pages` environment:
- `validate --scan --allow-empty --require-signature submissions` (unsigned bundles rejected)
- `build_index`
- `actions/attest-build-provenance@v2` on `site/index.json` + `site/index.html`
- GitHub Pages deploy from `site/`

## Key invariants

- `manifest.json` SHA256 entries must match actual file contents — the validator enforces this; never edit bundle files after the manifest is written.
- `signers.yaml` is the live policy; `signers.example.yaml` documents the schema shape. The validator reads neither — policy enforcement is a future CI step.
- `site/` is generated; do not hand-edit it. Rebuild via `build_index.py`.
- The `publish` job requires the `github-pages` environment to be configured with a required reviewer in repository settings.

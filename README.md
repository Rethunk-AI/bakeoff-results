# Rethunk Bakeoff Results

Private staging repository for publishing approved bakeoff result bundles from
`Rethunk-AI/bakeoff`.

This repository is private until the publication campaign is ready. Do not share
raw submissions, generated indexes, or dashboard artifacts outside the approved
release path.

## Bundle Layout

Each submitted bundle lives under `submissions/<publisher>/<run-id>/` and must
contain:

```text
result.json
manifest.json
summary.md
dashboard.html              # optional
signature.sigstore.json     # optional Sigstore/Rekor bundle
```

`manifest.json` uses schema version `bakeoff-results/v1` and lists SHA256
hashes for every bundle artifact except `manifest.json` itself:

```json
{
  "schema_version": "bakeoff-results/v1",
  "bundle": {
    "run_id": "run-2026-04-26-001",
    "timestamp": "2026-04-26T00:00:00Z"
  },
  "signer": {
    "identity": "github-actions[bot]",
    "issuer": "https://token.actions.githubusercontent.com",
    "repository": "Rethunk-AI/bakeoff",
    "policy": "bakeoff-results-signers/v1"
  },
  "files": {
    "result.json": { "sha256": "..." },
    "summary.md": { "sha256": "..." },
    "signature.sigstore.json": { "sha256": "..." }
  }
}
```

`result.json` must include `run_id`, `timestamp`, `provenance` with source
repository and commit, and either `model_ids` or `models` entries. The static
index also reads `judge_mode` or `judge.mode`, plus `config_hash`,
`config.hash`, or `config.sha256` when present.

## Commands

Validate all staged submissions:

```sh
PYTHONPATH=src python -m bakeoff_results.validate --scan --allow-empty submissions
```

Validate a single bundle:

```sh
PYTHONPATH=src python -m bakeoff_results.validate submissions/example/run-001
```

Build the static index:

```sh
PYTHONPATH=src python -m bakeoff_results.build_index --submissions submissions --site site
```

Run the local verification suite:

```sh
python -m compileall src tests
PYTHONPATH=src python -m unittest discover -s tests
```

Generated `site/index.html` and `site/index.json` are static, GitHub-first
publication artifacts. GitHub Pages deployment and artifact attestations are
deferred until repository settings and release policy are finalized.

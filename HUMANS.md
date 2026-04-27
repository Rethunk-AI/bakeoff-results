# Humans — bakeoff-results

Authoritative guide for running and operating this repository.

## Requirements

Python 3.11 or later. No runtime dependencies outside the standard library.

## Validate submissions

Scan all staged bundles:

```sh
PYTHONPATH=src python -m bakeoff_results.validate --scan --allow-empty submissions
```

Validate a single bundle directory:

```sh
PYTHONPATH=src python -m bakeoff_results.validate submissions/<publisher>/<run-id>
```

Require every scanned bundle to carry a Sigstore signature (used by the publish gate):

```sh
PYTHONPATH=src python -m bakeoff_results.validate --scan --require-signature submissions
```

## Build the static index

```sh
PYTHONPATH=src python -m bakeoff_results.build_index --submissions submissions --site site
```

Outputs `site/index.json` and `site/index.html`.

## Run the test suite

```sh
python -m compileall src tests
PYTHONPATH=src python -m unittest discover -s tests
```

## Verify published site artifacts

```sh
gh attestation verify site/index.json --repo Rethunk-AI/bakeoff-results
gh attestation verify site/index.html --repo Rethunk-AI/bakeoff-results
```

## Bundle layout

Each submission lives at `submissions/<publisher>/<run-id>/` and must contain:

```
result.json             required
manifest.json           required
summary.md              required
dashboard.html          optional
signature.sigstore.json optional — Sigstore/Rekor bundle
```

`manifest.json` uses schema version `bakeoff-results/v1` and records SHA256 hashes
for every bundle file except `manifest.json` itself:

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
    "result.json":              { "sha256": "..." },
    "summary.md":               { "sha256": "..." },
    "signature.sigstore.json":  { "sha256": "..." }
  }
}
```

`result.json` must include `run_id`, `timestamp`, `provenance` (with source
repository and commit), and either `model_ids` or `models`. The static index
also reads `judge_mode`/`judge.mode` and `config_hash`/`config.hash`/`config.sha256`
when present.

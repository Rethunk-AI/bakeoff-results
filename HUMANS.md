# Humans — bakeoff-results

## Requirements

Python 3.12+. Stdlib only — set `PYTHONPATH=src` for CLI commands.

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

## Test

```sh
python -m compileall src tests
PYTHONPATH=src python -m unittest discover -s tests
```

## Verify published site

```sh
gh attestation verify site/index.json --repo Rethunk-AI/bakeoff-results
gh attestation verify site/index.html --repo Rethunk-AI/bakeoff-results
```

## Bundle layout

`submissions/<publisher>/<run-id>/`: `result.json`, `manifest.json` (`bakeoff-results/v1`), and `summary.md` are required; `dashboard.html` and `signature.sigstore.json` are optional. `result.json` needs `run_id`, `timestamp`, `provenance`, and `model_ids` or `models`.

## Uninstall

No packages are installed. Delete the clone when finished.

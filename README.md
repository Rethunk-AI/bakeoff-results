<h1 align="center">Rethunk Bakeoff Results</h1>

<div align="center">

[![CI](https://github.com/Rethunk-AI/bakeoff-results/actions/workflows/ci.yml/badge.svg)](https://github.com/Rethunk-AI/bakeoff-results/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.12-blue)](pyproject.toml)

</div>

---

Private staging repo for approved bakeoff result bundles from `Rethunk-AI/bakeoff`.
Bundles are validated, indexed, and published to GitHub Pages after signer policy
and release approval checks pass.

## Quick start

```sh
PYTHONPATH=src python -m bakeoff_results.validate --scan --allow-empty submissions
```

Install, build, test, and verify: [HUMANS.md](HUMANS.md).

## Highlights

- **Validated bundles** — structural checks, SHA256 integrity, signer metadata on every submission
- **Static leaderboard** — `build_index.py` generates `site/index.json` and a filterable HTML explorer
- **Distributed worker queue** — optional HTTP API for runner registration, job claim, heartbeat, and signed submit; admin dashboard at `/runners`
- **Signer policy** — `signers.yaml` allowlist; Sigstore/Rekor material when present
- **Supply-chain posture** — CI verifies bundles, attests site artifacts, deploys under a protected environment
- **Governed lifecycle** — accepted, superseded, disputed, revoked states ([GOVERNANCE.md](GOVERNANCE.md))

## Documentation

| Doc | Audience |
| --- | --- |
| [HUMANS.md](HUMANS.md) | Validate, build, test, verify attestations, bundle schema |
| [AGENTS.md](AGENTS.md) | File layout, schema versions, CI jobs, invariants |
| [GOVERNANCE.md](GOVERNANCE.md) | Result states, signer policy, moderation standards |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting, threat scope, trust bootstrap |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Commits, hooks, PR process |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## License

MIT — see [LICENSE](LICENSE). © 2026 Rethunk-AI

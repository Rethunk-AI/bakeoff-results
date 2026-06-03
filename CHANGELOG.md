# Changelog

## Unreleased

## 2026-06-03

### Added
- Inline result-state and partial-score badges on leaderboard entries; failure reason surfaced in the actions menu; state and cohort filters added (closes #24, refs #9, #21)
- Multi-select filters, column visibility toggle, and column sort (closes #16, #17, #18)
- VRAM ranges, gear/settings panel, range sliders, Similar Results column, config-hash click-to-copy (closes #10)
- Filter bar smooth expand/collapse animation, chip-strip, and range sliders
- Non-blocking advisory `cosign verify-blob` step added to CI `verify` job (refs #23)
- `GOVERNANCE.md` documents `incomplete` and `failed` run-outcome states

### Changed
- Filter-add button repositioned to stable header row; `×` toggle symbol shown when multi-select is active; padding reduced (closes #29)
- Cohort filter column removed; slider centering and smooth collapse animation applied (refs #22)
- Filter chevron direction corrected; expand/collapse animation timing, margin, and fade polished
- Seven visual bugs fixed: col-vis-panel anchor, slider, badge visibility, hardware cell layout (refs #28)
- CI workflow Actions pins updated to reviewed versions; corrupted `setup-python` pins repaired
- Repo-ops dependencies and Actions versions upgraded

## 2026-04-27

### Added
- Three-tier doc governance: `HUMANS.md`, `AGENTS.md`, `CLAUDE.md` symlink
- Repo hygiene: `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODEOWNERS`, PR template, dependabot, issue templates

### Changed
- `README.md` stripped to orientation + links per Bastion doc-governance tier rules

## 2026-04-26

### Added
- `signers.yaml` — approved signer allowlist locked to `Rethunk-AI/bakeoff` main, `package-results.yml`, `github-actions[bot]` OIDC identity
- `--require-signature` flag on `bakeoff-results-validate` — publish gate rejects unsigned bundles; staging remains permissive
- CI `publish` job — signature gate, `actions/attest-build-provenance@v2` for `site/index.json` + `site/index.html`, GitHub Pages deploy under protected `github-pages` environment
- Initial scaffold: bundle validator, index builder, CI, docs (`bakeoff-results/v1` schema)
- Compatibility fix for current `Rethunk-AI/bakeoff` payload shape

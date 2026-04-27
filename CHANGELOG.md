# Changelog

## Unreleased

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

# Rethunk Bakeoff Results

Private staging repository for publishing approved bakeoff result bundles from
`Rethunk-AI/bakeoff`. Bundles are validated, indexed, and published to GitHub
Pages after signer policy and release approval checks pass.

## Documentation

- [HUMANS.md](HUMANS.md) — validate, build, test, verify attestations, bundle schema
- [AGENTS.md](AGENTS.md) — file layout, schema versions, submission lifecycle, CI jobs
- [GOVERNANCE.md](GOVERNANCE.md) — result states, signer policy, moderation standards
- [SECURITY.md](SECURITY.md) — vulnerability reporting, threat scope, trust bootstrap

## Security Model

This repository validates and publishes bakeoff results through a cryptographically
verified supply chain. Integrity depends on three layers:

1. **Signer Policy** (`signers.yaml`) — Allowlist of trusted identities and issuers
   - Plain YAML file with no embedded signature
   - Trusted via **git commit signatures only**
   - Any change must come from a signed commit by an existing signer
   - Verify: `git log --format=fuller --all -- signers.yaml`

2. **Bundle Attestation** — Submitted results must include `signature.sigstore.json`
   - Validated against trusted signers in the policy file
   - Checked for transparency-log consistency (Rekor)
   - CI validates schema presence; full Sigstore verification in progress

3. **Artifact Attestation** — Generated index artifacts (published to Pages)
   - Attested with GitHub build provenance
   - Links to workflow run and commit history
   - Immutable once published

For details on threat scope and vulnerability reporting, see [SECURITY.md](SECURITY.md).

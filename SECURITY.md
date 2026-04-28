# Security Policy

This repository is a results archive and publication pipeline. It accepts
signed result bundles, validates them, generates a static leaderboard, and
publishes via GitHub Pages. The threat surface is narrow but real: malicious
bundles, validator bypass, and signer policy compromise.

## Supported versions

Only `main` is supported.

## Reporting a vulnerability

**Do not open a public issue** for security-sensitive findings. Report
privately via GitHub's Security tab:

<https://github.com/Rethunk-AI/bakeoff-results/security/advisories/new>

Please include:

- Affected commit SHA
- Reproduction steps
- Observed vs. expected behavior
- Impact assessment

## Trust Bootstrap: signers.yaml

The file `signers.yaml` defines the allowlist of trusted signers and issuing
authorities. Because this file itself is plain YAML with no cryptographic
signature, it relies on **git commit integrity** for trust. Any modification to
`signers.yaml` must come from a signed commit (GPG or SSH).

**For consumers:** Verify that the commit introducing or last modifying
`signers.yaml` is cryptographically signed:

```bash
git log --format=fuller --all -- signers.yaml | head -20
# Look for "Commit:" line with "gpgsig" or "ssh" prefix
```

**For maintainers:** All commits modifying `signers.yaml` should be signed by at
least one existing trusted signer. Unsigned or unverified changes to this file
represent a policy compromise and must be reverted immediately.

## In scope

- Path traversal or arbitrary file read/write via a crafted `manifest.json`
  (e.g. `../` in file paths)
- Schema or hash-validation bypass that allows a tampered bundle to pass
  `validate.py`
- Signer policy bypass that allows an unauthorized identity to have its
  submission accepted (including unsigned modifications to `signers.yaml`)
- Malicious bundle content that causes the index builder or static site to
  execute or inject code
- Credential or token leakage through CI logs or generated site artifacts

## Out of scope

- Vulnerabilities in Sigstore, Rekor, or `cosign` themselves (report upstream)
- GitHub Actions runner or Pages infrastructure issues (report to GitHub)
- Fabricated benchmark results that pass all cryptographic checks — this is an
  anti-tamper system, not a remote execution verifier; see `GOVERNANCE.md` for
  the dispute/revocation process
- Denial of service via an oversized bundle (resource-sizing, not a
  vulnerability)

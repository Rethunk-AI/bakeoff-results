# TODO

Future backlog only. Current work is tracked in GitHub Issues.

## Publication pipeline

- [ ] Full Sigstore/Rekor verification of `signature.sigstore.json` in CI — currently structural presence only; needs `cosign verify-blob` wired in once the submitting workflow stabilises
- [ ] Resolve GitHub Pages activation via Actions token — tracked in [#2](https://github.com/Rethunk-AI/bakeoff-results/issues/2)

## Leaderboard

- [ ] Cohort filtering — gate aggregate rankings by compatible config/task/judge/schema to prevent misleading cross-run comparisons
- [ ] Disputed/superseded/revoked state badges on index entries

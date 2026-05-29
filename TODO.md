# TODO

Future backlog only. Current work is tracked in GitHub Issues.

## Publication pipeline

- [ ] Full Sigstore/Rekor verification of `signature.sigstore.json` in CI — advisory `cosign verify-blob` step now exists in the `verify` job (`continue-on-error: true`, refs [#23](https://github.com/Rethunk-AI/bakeoff-results/issues/23)); remaining work is promoting it to a hard gate once upstream Rethunk-AI/bakeoff emits real Sigstore bundles
- [ ] Resolve GitHub Pages activation via Actions token — tracked in [#2](https://github.com/Rethunk-AI/bakeoff-results/issues/2)

## Leaderboard

- [ ] Cohort filtering — gate aggregate rankings by compatible config/task/judge/schema to prevent misleading cross-run comparisons
- [ ] Disputed/superseded/revoked state badges on index entries

# TODO

Future backlog only. Current work is tracked in GitHub Issues.

## Publication pipeline

- [ ] Full Sigstore/Rekor verification of `signature.sigstore.json` in CI — advisory `cosign verify-blob` step now exists in the `verify` job (`continue-on-error: true`, refs [#23](https://github.com/Rethunk-AI/bakeoff-results/issues/23)); remaining work is promoting it to a hard gate once upstream Rethunk-AI/bakeoff emits real Sigstore bundles
- [ ] Resolve GitHub Pages activation via Actions token — tracked in [#2](https://github.com/Rethunk-AI/bakeoff-results/issues/2)

## Leaderboard

- [ ] Remove `cohort` field — drop from schema, `build_index.py`, and `site/index.html` once all synthetic records are purged; field must never appear in real result records (see [#22](https://github.com/Rethunk-AI/bakeoff-results/issues/22))

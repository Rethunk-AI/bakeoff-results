# Governance

This repository publishes bakeoff results only after evidence review and signer
policy checks. The initial release path is private and GitHub-first: validate
submitted bundles, generate static index artifacts, then publish once release
approval is granted.

## Signer Policy

Accepted submissions must come from the approved `Rethunk-AI/bakeoff` release
workflow or another explicitly allowlisted identity. The current placeholder
allowlist shape is documented in `signers.example.yaml`.

Bundles may include `signature.sigstore.json` with Sigstore/Rekor verification
material. The local validator checks that signer metadata and transparency-log
material are structurally present; CI should later add full Sigstore verification
and GitHub artifact attestations for generated index artifacts.

## Result States

Every published run should be treated as one of these states:

- `accepted`: evidence and signer policy passed review.
- `superseded`: a newer bundle replaces an earlier accepted run.
- `disputed`: credible evidence challenges the result or presentation.
- `revoked`: the bundle failed policy, integrity, provenance, or moderation
  review after publication.

State changes must be evidence-backed and recorded with the affected run ID,
reason, reviewer, timestamp, and links to supporting material.

## Moderation Standards

Results are about artifacts, methods, and evidence. Do not use the index,
summary pages, pull requests, or release notes for personal shaming, dogpiling,
or unsupported claims about individuals or teams.

Moderation decisions should cite concrete evidence: bundle hashes, provenance,
source commits, workflow runs, signatures, issue or PR discussions, and reviewer
notes. Ambiguous cases should be marked `disputed` until the evidence is clear.

# Contributing

Thin routing doc. Setup and commands live in [`HUMANS.md`](HUMANS.md);
internals and invariants live in [`AGENTS.md`](AGENTS.md); result states and
moderation policy live in [`GOVERNANCE.md`](GOVERNANCE.md). Don't re-derive
that content here.

## Where to start

- **Run/validate/test:** [`HUMANS.md`](HUMANS.md)
- **File layout, schema versions, CI jobs:** [`AGENTS.md`](AGENTS.md)
- **Result states, signer policy, moderation:** [`GOVERNANCE.md`](GOVERNANCE.md)

## Development setup

No install required. The validator and index builder use stdlib only and run
via `PYTHONPATH`:

```sh
git clone https://github.com/Rethunk-AI/bakeoff-results
cd bakeoff-results
# No pip install needed for development — all commands use PYTHONPATH=src
```

Alternatively, install in editable mode for the packaged entry-points:

```sh
pip install -e .
bakeoff-results-validate --help
bakeoff-results-build-index --help
```

## Before opening a PR

```sh
python -m compileall src tests
PYTHONPATH=src python -m unittest discover -s tests
```

## Commits and PRs

- **Conventional Commits**: `type(scope): subject`. Types: `feat`, `fix`,
  `docs`, `ci`, `chore`, `test`, `refactor`. Scope: module (`validate`,
  `build_index`), doc tier (`readme`, `agents`, `humans`), or policy (`signers`,
  `governance`).
- **Body explains why**, not what.
- **One logical unit per commit.**

## What NOT to change without discussion

- The `bakeoff-results/v1` schema — downstream tooling in `Rethunk-AI/bakeoff`
  reads it.
- The `bakeoff-results-signers/v1` signer policy shape — changing it silently
  breaks the trust model.
- The three-tier doc split. If a fourth top-level `*.md` seems necessary, open
  an issue first.

## Security

See [`SECURITY.md`](SECURITY.md) for private disclosure. Do not file public
issues for vulnerabilities.

## What and why

<!-- What does this change? Why? -->

## Testing

- [ ] `python -m compileall src tests`
- [ ] `PYTHONPATH=src python -m unittest discover -s tests`

## Checklist

- [ ] Schema versions (`bakeoff-results/v1`, `bakeoff-results-signers/v1`) unchanged or bumped with a migration path
- [ ] No setup/install content added to `AGENTS.md`; no internals added to `HUMANS.md`
- [ ] `GOVERNANCE.md` updated if result states or signer policy changed

# Contributing

Thank you for looking. This is a personal portfolio project, but issues and pull requests are welcome.

## Set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
adl gate        # builds everything offline and runs the release gate
pytest -q       # the full test suite, offline
```

No cloud account, API key or network access is needed.

## Rules for changes

1. **Code decides, the model narrates.** Numbers and actions come from code. A model may only explain them. See [ADR 0001](docs/adr/0001-code-decides-model-narrates.md).
2. **Agents read only through the gateway.** No direct table access from agent code. See [ADR 0002](docs/adr/0002-data-products-through-one-gateway.md).
3. **No invented numbers.** Every number in the docs comes from a command. Use an `<!-- output: ... -->` block and run `python scripts/render_docs.py`. CI fails if a block is stale.
4. **Honest labels.** Code that has never run against a real service is labelled *written, not run*. Nothing is described as deployed.
5. **Fictional data only.** Use `.example` domains and 555-01xx phone numbers. Never add real company names, people or data.
6. **Docs travel with code.** A new file goes into its folder README's file table; a new component gets a doc in `docs/components/` with the standard sections. The hygiene tests check this.

## Before you open a pull request

```bash
ruff check . && ruff format --check .
python scripts/render_docs.py --check
pytest -q
```

Add a line under `## Unreleased` in [CHANGELOG.md](CHANGELOG.md).

## Adding a domain

Follow [docs/adding-a-domain.md](docs/adding-a-domain.md).

## Security

Do not report security problems in public issues. See [SECURITY.md](SECURITY.md).

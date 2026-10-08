# scripts

Repository tooling.

| File | What it does |
|---|---|
| `render_docs.py` | Regenerates every `<!-- output: -->` and `<!-- code: -->` block in the docs from the CLI and source; `--check` fails CI on drift |
| `overlap_check.py` | Reports long word sequences shared with a local reference text, to confirm nothing is copied |

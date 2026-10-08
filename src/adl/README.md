# src/adl

The `adl` package. `core/`, `knowledge/`, `serve/` and `storage/` are domain-neutral; `domains/` holds the business domains.

| File | What it does |
|---|---|
| `__init__.py` | Package root and repository path |
| `cli.py` | The `adl` command line: every step, report and the release gate |
| `iac.py` | Static summary of Terraform, Bicep and workflows for `adl iac` |
| `core/` | Contracts, quality, lineage, metrics layer, guardrails, gateway, audit, domain registry, model client |
| `domains/` | Business domains: retail, mortgage and insurance built, healthcare planned |
| `knowledge/` | Embeddings, knowledge graph, retrieval, Azure AI Search adapter |
| `serve/` | MCP server and A2A endpoint |
| `storage/` | Local Delta Lake store and four cloud adapters |

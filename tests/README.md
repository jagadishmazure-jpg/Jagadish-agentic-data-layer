# tests

The test suite: `pytest -q`. Everything runs offline; the full suite took about a minute on the build machine.

| File | What it does |
|---|---|
| `conftest.py` | Shared fixtures: the lake, the valued lake and a fresh gateway |
| `test_adapters.py` | Every storage adapter: interface, round trip, parameters, secretless, platform specifics |
| `test_agentflow.py` | The shared approval workflow core (digest, validator, fallback) and FOCUS cost rows |
| `test_agents.py` | Workflow, approvals, digest, validator, fallback, injection matrix |
| `test_cli.py` | Every command runs; FOCUS CSV; a missed target is reported; the gate passes |
| `test_contracts.py` | Every contract validates; one test per validation rule |
| `test_gateway.py` | Attack suite, PII scan, tamper detection, row and column security, filters |
| `test_iac.py` | Terraform, Bicep, workflows and Dependabot structure and security properties |
| `test_knowledge.py` | Retrieval quality, embeddings, graph, region trimming, Azure AI Search adapter |
| `test_mortgage.py` | Mortgage end to end: pipeline, point-in-time view, model, policy, gateway, knowledge, agents, value, CLI |
| `test_models.py` | Forecast, stockout risk, elasticity, markdown; no ground truth in product code |
| `test_pipeline.py` | Quarantine, dedup, PII split, redaction, k-anonymity, Delta, determinism |
| `test_quality_lineage_semantic.py` | Quality checks and freshness, OpenLineage events, metrics layer |
| `test_render_docs.py` | The doc renderer's output blocks and code excerpts |
| `test_repo_hygiene.py` | Docs complete and current, no dates or secrets, honest claims, real test count |
| `test_serve.py` | MCP tools and errors, A2A card, messages, errors and HTTP |
| `test_value.py` | Value ledger, intervals, KPI results, cost, FOCUS rows |

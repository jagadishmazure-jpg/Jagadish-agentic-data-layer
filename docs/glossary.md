# Glossary

| Term | Meaning here |
|---|---|
| A2A | Agent2Agent protocol: JSON-RPC between agents, with an agent card that advertises skills and auth. Here a data-provider endpoint in `src/adl/serve/a2a.py`. |
| Acceptable use | The purposes a data product may and may not be used for, stated in its contract and enforced by the gateway. |
| Agent identity | The name an agent reads data as (for example `agent:replenishment`); in Azure an Entra ID agent or managed identity. |
| Approval digest | A SHA-256 of the exact actions awaiting approval; the approver must quote it, so an approval cannot apply to a different set of actions. |
| AUC | Area under the ROC curve: the chance a random sell-out is ranked above a random non-sell-out. |
| Bronze, silver, gold | Medallion layers: as sent, conformed and checked, and data products for consumers. |
| Brier score | Mean squared error of probabilities; lower is better; shows calibration. |
| Common random numbers | Running two policies on identical randomness so the difference is caused by the policy alone. |
| Data contract | YAML promise for a table: owner, schema, PII flags, checks, SLOs, classification and acceptable use. |
| Data gateway | `DataGateway` in `src/adl/core/access.py`: the only way an agent reads data. |
| Data product | A gold table with a contract that sets `agent_exposed: true`. |
| Difference-in-differences | Comparing the change in test stores with the change in other stores over the same weeks, to isolate a price effect. |
| Dry run | The executor writes what it would do to the audit chain and does nothing else. |
| Elasticity | Percentage change in demand for a 1% change in price; negative for normal goods. |
| FOCUS | FinOps Open Cost and Usage Specification: a common column set for cost data. |
| GraphRAG local search | Retrieval that links a question to entities and follows their relationships to find documents. |
| Hash chain | Each audit record stores the hash of the previous one, so edits break verification. |
| Hybrid retrieval | Reciprocal rank fusion of vector and graph rankings. |
| k-anonymity | Suppressing groups smaller than k (10 here) so no row describes an individual. |
| MAF | Microsoft Agent Framework: agents, chat clients and graph workflows with human-in-the-loop requests. |
| MCP | Model Context Protocol: tools a model client can call. Here five read-only tools over the gateway. |
| Markdown | A price reduction on near-date stock. |
| MRR, nDCG, recall@k | Retrieval measures: rank of the first relevant document, ranking quality, and the share of relevant documents in the top k. |
| OIDC | OpenID Connect federation: CI exchanges a short-lived token for cloud credentials, so no secret is stored. |
| OpenLineage | Open standard for lineage run events (START, COMPLETE, FAIL) with input and output datasets. |
| Pseudonym | HMAC of a customer id with a key held by the privacy office. |
| Quarantine | Rows that fail a row-level contract check, kept in `silver.quarantine_<table>` instead of being dropped. |
| Release gate | `adl gate`: 38 checks (15 retail, 11 mortgage, 12 insurance) that must all pass. |
| Row scope | Row-level security: the region a copilot may see. |
| Stockout | A store-product with no stock left at close. |
| Value ledger | `gold.value_ledger`: value per lever and metric with a 95% interval, ids VL-RET-001 to 012. |
| WAPE | Weighted absolute percentage error: total absolute error divided by total actual. |

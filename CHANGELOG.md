# Changelog

All notable changes to this project are listed here.

## Unreleased

- Retail domain for the fictional Wrenfield Grocers: simulated grocer with ground truth, bronze/silver/gold pipeline with data contracts, quality checks, quarantine, PII split and OpenLineage events.
- Demand forecast, sell-out risk and markdown models, each backtested against a simple baseline.
- Data gateway with agent identities, purposes, row and column security, typed filters and a hash-chained audit log; 14-attempt attack suite.
- Hybrid knowledge retrieval (vector plus knowledge graph) with region trimming and injection screening.
- Agent Framework workflow: code plans, the model narrates, policy checks, approval bound to an action digest, dry-run execution.
- Value case, value ledger with bootstrap intervals, KPI results, cost per outcome and FOCUS cost rows.
- MCP server and A2A endpoint over the gateway.
- Storage adapters for OneLake, Databricks, BigQuery and AWS (written, not run) behind one interface.
- Terraform for Azure, Google Cloud and AWS, Bicep for Azure, hardened workflows; validated in CI, never applied.
- Release gate and full documentation set; mortgage, insurance and healthcare domains planned.

# Value principles

The five ways AI projects fail to create value, and what this repository does about each. The rest of the page shows where each principle lives in the code.

## Five mistakes and what this repository does about each

| Mistake | What it looks like | Answer in this repository | Evidence |
|---|---|---|---|
| 1. Treating AI as the goal rather than a tool for a business result | Projects named after a technology | The project is named after a decision (what to order and mark down), with a sponsor and decision owners | [value-case.md](value-case.md) |
| 2. No clear path from data to value | Insights that never change an action | The value tree links each data feed to an insight, an action, a value and a money line; every executed action carries a ledger id | `config/value-case.yaml`, `adl value` |
| 3. Staying stuck in pilots | Each pilot builds its own data plumbing | A domain-neutral core (contracts, gateway, metrics, MCP/A2A), portable storage, three clouds in IaC, a release gate | [architecture.md](architecture.md), [adding-a-domain.md](adding-a-domain.md) |
| 4. Overlooking business-model change | AI used only to do today's work faster | The data products and A2A endpoint let partners consume the layer; four business models are mapped | [ai-business-models.md](ai-business-models.md) |
| 5. Mistaking productivity for value | Counting briefs written or hours saved | The ledger counts dollars of margin with intervals, net of AI and platform cost; a missed target is reported | [metrics.md](metrics.md) |

## Mistake 1 in more detail: three implementation principles

| Principle | Here |
|---|---|
| Build advanced data capabilities first | Phases 1 and 2 (contracts, quality, lineage, models with backtests) come before any agent |
| Bring every stakeholder along, including for feedback on model performance | Store managers approve and reject lines; rejections are in the audit chain; the RACI in [operating-model.md](operating-model.md) names who reviews model quality |
| Back work that clearly raises revenue or lowers cost | The release gate fails unless the net-value interval is above zero |

## Mistake 5 in more detail: tools versus solutions

Generative AI used as a personal productivity tool is different from a tailored solution built
into processes and systems. The store brief here is the second kind: it is wired into the replenishment
workflow, constrained by code, validated, and its value is measured in margin, not in minutes saved.

## Getting out of pilots: the four enablers

| Enabler | In this repository |
|---|---|
| Align AI with strategy | Phase 0 value case with KPIs and owners before any model; the release gate fails if the value interval is not above zero |
| Modular, interoperable platforms and data ecosystems | Data products behind one gateway; MCP and A2A interfaces; one storage interface with local, Fabric, Databricks, BigQuery and S3 adapters |
| AI-ready roles | RACI for data product owners, stewards, agent owners, approvers and the platform team | 
| Compliant, human-centred AI | Acceptable-use purposes in contracts, PII split and redaction, approval by a named person, an audit chain, a threat model |

The enabler rows are detailed in [operating-model.md](operating-model.md) and
[data-governance.md](data-governance.md).

## The five steps from data to money

The healthcare domain (Halsey Vale Health, fully synthetic and PHI-free) predicts which hospital
patients are likely to fall and shows all five steps end to end: 9 feeds collected under contract, a
fall-risk model that beats the Morse total (AUC 0.650 against 0.506), four nursing measures each
approved by a nurse, a value ledger with intervals and a cost per outcome. See
[healthcare/README.md](healthcare/README.md) and the README's cross-domain table.

| Step | Here | Command |
|---|---|---|
| Collect the right data | 14 feeds, contracts, quarantine, lineage | `adl run`, `adl quality`, `adl lineage` |
| Generate insights | forecast, risk, elasticity, markdown, retrieval | `adl forecast`, `adl stockout`, `adl elasticity`, `adl markdown`, `adl retrieval` |
| Take action | approval-gated agent workflow | `adl agents` |
| Create value | value ledger with intervals | `adl value` |
| Monetise | value after cost, cost per outcome, FOCUS rows | `adl value`, `adl focus` |

## The four data capabilities to invest in

| Capability | Here |
|---|---|
| Data science | Rolling-origin backtests against baselines, difference-in-differences elasticity, bootstrap intervals |
| Data management | Contracts with owners, quality checks, SLOs, quarantine, lineage |
| Data platforms | Delta Lake + DuckDB locally; Fabric, Databricks, BigQuery and S3 adapters; Terraform and Bicep |
| Acceptable data use | Purposes allowed and prohibited per product, column and row security, k-anonymity for segments, PII never exposed |

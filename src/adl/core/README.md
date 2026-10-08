# src/adl/core

Domain-neutral platform code shared by every domain.

| File | What it does |
|---|---|
| `__init__.py` | Package marker |
| `agentflow.py` | Domain-neutral approval workflow: plan, narrate, validate, approval gate bound to a digest, dry-run execute |
| `access.py` | `DataGateway`: identity, grants, purposes, row and column security, typed filters, caps, audit |
| `audit.py` | Hash-chained audit log with verification |
| `contracts.py` | Contract model, validation rules, SQL predicate for quarantine |
| `domain.py` | Domain registry: built and planned domains with use case, KPIs and levers |
| `finops.py` | AI and platform cost estimate and FOCUS 1.0 rows |
| `guardrails.py` | Injection screen, PII redaction, untrusted quoting, Prompt Shields request |
| `lineage.py` | OpenLineage run events, validation, upstream walk |
| `logit.py` | Shared L2 logistic regression, top driver per row and AUC (used by insurance) |
| `llm.py` | Chat client selection (offline mock or Foundry) and token estimate |
| `quality.py` | Quality checks and SLOs evaluated with SQL |
| `semantic.py` | Metrics layer: metric definitions compiled to parameterised SQL |

# src/adl/core

Domain-neutral platform code shared by every domain.

| File | What it does |
|---|---|
| `__init__.py` | Package marker |
| `access.py` | `DataGateway`: identity, grants, purposes, row and column security, typed filters, caps, audit |
| `audit.py` | Hash-chained audit log with verification |
| `contracts.py` | Contract model, validation rules, SQL predicate for quarantine |
| `domain.py` | Domain registry: built and planned domains with use case, KPIs and levers |
| `guardrails.py` | Injection screen, PII redaction, untrusted quoting, Prompt Shields request |
| `lineage.py` | OpenLineage run events, validation, upstream walk |
| `llm.py` | Chat client selection (offline mock or Foundry) and token estimate |
| `quality.py` | Quality checks and SLOs evaluated with SQL |
| `semantic.py` | Metrics layer: metric definitions compiled to parameterised SQL |

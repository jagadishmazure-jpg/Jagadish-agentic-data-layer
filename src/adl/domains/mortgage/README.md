# src/adl/domains/mortgage

Built: the mortgage domain for the fictional Quillmere Home Loans (rate-lock fallout). Data definitions
are in [domains/mortgage](../../../../domains/mortgage/README.md) and the write-up in
[docs/mortgage](../../../../docs/mortgage/README.md).

| File | What it does |
|---|---|
| `__init__.py` | Package docstring |
| `world.py` | Seeded daily simulator: applications, locks, documents, stages, outcomes; hidden borrower traits; the current rules |
| `synth.py` | 180-day history and the 10 bronze feeds with planted faults and pipeline notes |
| `pipeline.py` | Bronze landing, silver conform and quarantine, gold products, the point-in-time view rebuilt from silver |
| `risk.py` | Fallout-risk features, logistic model, drivers, bands and the backtest against the expiry rule |
| `policy.py` | Shared decision policy: who gets calls, chases and extensions (used by the agent and the simulation) |
| `simulate.py` | Paired forward simulation over 30 seeds with bootstrap intervals |
| `knowledge.py` | Corpus, evaluation questions and knowledge graph for retrieval |
| `agents.py` | Plans per loan officer through the gateway and the approval workflow spec |
| `attacks.py` | The 14 access attacks and the PII scan |
| `value.py` | Value case baselines, value ledger, KPI results, cost and FOCUS rows |
| `runtime.py` | Builds and caches the lake, model, gateway and evaluations |
| `report.py` | The `adl mortgage STEP` reports and the 11 mortgage gate checks |

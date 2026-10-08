# src/adl/domains/insurance

Built: the insurance domain for the fictional Ferrowind Insurance (claims triage and leakage). Data
definitions are in [domains/insurance](../../../../domains/insurance/README.md) and the write-up in
[docs/insurance](../../../../docs/insurance/README.md).

| File | What it does |
|---|---|
| `__init__.py` | Package docstring |
| `world.py` | Seeded daily simulator: claims, queues, payments, audits, recoveries; hidden claim traits; synthetic proxy group; the current rules |
| `synth.py` | 180-day history, synthetic claimants and the 12 bronze feeds with planted faults and claim notes |
| `pipeline.py` | Bronze landing, silver conform and quarantine, gold products, the point-in-time view rebuilt from silver |
| `models.py` | Complexity, leakage and subrogation features, datasets, models and backtests against today's rules |
| `policy.py` | Shared decision policy: queue assignment with capacity guards, reviews and referrals (used by the agent and the simulation) |
| `simulate.py` | Paired forward simulation over 30 seeds with bootstrap intervals and per-group counters |
| `knowledge.py` | Corpus, evaluation questions and knowledge graph for retrieval |
| `agents.py` | Plans per office through the gateway, the approval workflow spec and the simulated team lead |
| `attacks.py` | The 14 access attacks and the PII scan |
| `value.py` | Value case baselines, value ledger, KPI results, cost and FOCUS rows |
| `fairness.py` | Unfair-discrimination screen: history from the monitor, forward from the simulation |
| `runtime.py` | Builds and caches the lake, models, score products, gateway and evaluations |
| `report.py` | The `adl insurance STEP` reports and the 12 insurance gate checks |

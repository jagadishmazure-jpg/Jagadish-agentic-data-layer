# src/adl/domains/healthcare

Built: the healthcare domain for the fictional Halsey Vale Health (inpatient fall risk), on fully
synthetic, PHI-free data. Data definitions are in [domains/healthcare](../../../../domains/healthcare/README.md)
and the write-up in [docs/healthcare](../../../../docs/healthcare/README.md).

| File | What it does |
|---|---|
| `__init__.py` | Package docstring |
| `world.py` | Seeded daily ward simulator: admissions, hidden traits, fall hazards by mechanism, measures and their effects, documentation gap, today's Morse rule |
| `synth.py` | 180-day history, synthetic patients with reserved contact details and the 9 bronze feeds with planted faults and nursing notes |
| `pipeline.py` | Bronze landing, silver conform and quarantine, note redaction, pseudonyms, gold products, the morning view rebuilt from silver |
| `models.py` | Fall-risk features, patient-mornings, the model, its backtest against the Morse total and rule, bands |
| `policy.py` | Shared decision policy: benefit per measure, capacity filling, the agent policy used by the assistant and the simulation |
| `simulate.py` | Paired forward simulation over 30 seeds with bootstrap intervals and per-group counters |
| `knowledge.py` | Corpus, evaluation questions and knowledge graph for retrieval |
| `agents.py` | Plans per ward through the gateway, the approval workflow spec (four nursing kinds) and the simulated nurse in charge |
| `attacks.py` | The 14 access attacks and the patient-identifier scan |
| `value.py` | Value case baselines, value ledger, KPI results, cost and FOCUS rows |
| `fairness.py` | Fairness screen: history from the monitor, forward from the simulation |
| `runtime.py` | Builds and caches the lake, model, worklist, gateway and evaluations |
| `report.py` | The `adl healthcare STEP` reports and the 13 healthcare gate checks |

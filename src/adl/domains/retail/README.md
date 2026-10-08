# src/adl/domains/retail

The built retail domain for the fictional Wrenfield Grocers. Component docs: [docs/components/](../../../../docs/components/README.md).

| File | What it does |
|---|---|
| `__init__.py` | Package marker |
| `world.py` | The simulated grocer: stores, products, suppliers, calendar, daily engine, current rules |
| `synth.py` | 140-day history landed as bronze feeds with realistic faults; ground truth for evaluation only |
| `pipeline.py` | Bronze, silver and gold with quarantine, PII split, redaction, k-anonymity and lineage |
| `features.py` | Dense store-product x day arrays for the models |
| `forecast.py` | Global ridge demand forecast and rolling-origin backtest |
| `stockout.py` | Three-day sell-out probability, delivery timing, backtest against the cover rule |
| `markdown.py` | Elasticity from price tests, markdown response, minimum-clearing discount |
| `policy.py` | Replenishment, transfers and markdown policy shared by agents and simulation |
| `insights.py` | Insight data products: forecast, stockout risk, markdown candidates |
| `knowledge.py` | Retail aliases, corpus and evaluation loading, graph and index build |
| `agents.py` | Agent Framework workflow: plan, narrate, policy, approval gate, dry-run execute |
| `attacks.py` | The 14-attempt access attack suite, PII scan and tamper check |
| `simulate.py` | Forward value simulation with common random numbers and bootstrap intervals |
| `value.py` | Value case, value ledger, KPI results, cost estimate, FOCUS rows |
| `runtime.py` | Cached builders for the lake, value, backtests, agent run and injection evaluation |

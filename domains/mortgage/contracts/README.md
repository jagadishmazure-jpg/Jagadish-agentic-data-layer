# domains/mortgage/contracts

Planned contracts for the fictional Quillmere Home Loans. They validate in CI with `status: planned`; nothing is built.

| File | What it does |
|---|---|
| `gold.fallout_risk.yaml` | Probability each locked application falls out before closing, with the main drivers (internal, agent-exposed, planned) |
| `gold.pipeline_daily.yaml` | Applications per stage, product and channel per day with pull-through and cycle time (internal, agent-exposed, planned) |
| `silver.applications.yaml` | Loan applications with stage dates, product, rate lock and broker channel (restricted, planned) |

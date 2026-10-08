# domains/healthcare/contracts

Planned contracts for the fictional Halsey Vale Health. They validate in CI with `status: planned`; nothing is built.

| File | What it does |
|---|---|
| `gold.fall_risk_worklist.yaml` | Current inpatients ranked by fall risk for the nurse in charge; pseudonymous encounter ids only (confidential, agent-exposed, planned) |
| `gold.ward_fall_rates.yaml` | Falls per 1,000 bed-days per ward and week, suppressed below the minimum count (confidential, agent-exposed, planned) |
| `silver.inpatient_encounters.yaml` | Inpatient stays with ward, mobility assessment, medication classes and fall events (synthetic only) (restricted, planned) |

# domains/insurance/contracts

Planned contracts for the fictional Ferrowind Insurance. They validate in CI with `status: planned`; nothing is built.

| File | What it does |
|---|---|
| `gold.claims_triage.yaml` | Complexity score and suggested handling queue for each open claim (internal, agent-exposed, planned) |
| `gold.leakage_signals.yaml` | Closed claims whose payment pattern suggests leakage (overpayment, missed subrogation, duplicate invoices) (internal, agent-exposed, planned) |
| `silver.claims.yaml` | First notice of loss and claim events with coverage, reserve and adjuster notes (restricted, planned) |

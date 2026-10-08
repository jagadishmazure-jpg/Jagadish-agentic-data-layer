# config/insurance

Business configuration for the insurance domain, read by code. Agents cannot change these files.
`value-case.yaml` and `fairness.yaml` were committed before the simulator, the models or any result.

| File | What it does |
|---|---|
| `agents.yaml` | Insurance agent identities: products, purposes, region row scope, denied columns, row caps; knowledge purposes |
| `fairness.yaml` | Unfair-discrimination screen: synthetic proxy groups, reference group, selection-rate ratio band, cycle-days gap |
| `policy.yaml` | Queue capacity, planning figures, triage thresholds (chosen on tuning seeds), approval thresholds, dry-run mode |
| `value-case.yaml` | Phase 0 value case: problem, owners, KPIs with targets set before results, value tree |

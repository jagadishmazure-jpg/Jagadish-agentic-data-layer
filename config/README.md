# config

Business configuration read by code. Agents cannot change these files.

| File | What it does |
|---|---|
| `mortgage/` | Mortgage domain configuration: identities, policy, value case |
| `insurance/` | Insurance domain configuration: identities, policy, value case, fairness limits |
| `agents.yaml` | Agent identities: granted products, purposes, row scope, denied columns, row caps; knowledge purposes |
| `policy.yaml` | Decision policy: service levels, markdown cap, transfer rules, approval thresholds, dry-run mode |
| `pricing.yaml` | Assumed unit rates for the cost-per-outcome estimate (not quotes) |
| `value-case.yaml` | Phase 0 value case: problem, owners, KPIs with targets, value tree |

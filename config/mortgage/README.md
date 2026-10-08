# config/mortgage

Business configuration for the mortgage domain, read by code. Agents cannot change these files.

| File | What it does |
|---|---|
| `agents.yaml` | Mortgage agent identities: products, purposes, region row scope, denied columns, row caps; knowledge purposes |
| `policy.yaml` | Call and chase capacity, extension rules, the $400 approval threshold for extension fees, dry-run mode |
| `value-case.yaml` | Phase 0 value case: problem, owners, KPIs with targets set before results, value tree |

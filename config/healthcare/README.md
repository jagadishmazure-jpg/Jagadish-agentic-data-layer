# config/healthcare

Business configuration for the healthcare domain (fully synthetic, PHI-free data), read by code.
Agents cannot change these files. `value-case.yaml` and `fairness.yaml` were committed before the
simulator, the model or any result.

| File | What it does |
|---|---|
| `agents.yaml` | Healthcare identities under minimum necessary: products, purposes, site row scope, denied and masked columns, row caps; knowledge purposes |
| `fairness.yaml` | Fairness screen: synthetic patient groups, reference group, ratio band, protection before a fall, falls-rate gap |
| `policy.yaml` | The four nursing measures, capacity, planning costs and effects, capacity filling, risk multiplier (chosen on tuning seeds), nurse approval, dry-run mode |
| `value-case.yaml` | Phase 0 value case: problem, owners, KPIs with targets set before results, value tree, non-goals (nothing clinical) |

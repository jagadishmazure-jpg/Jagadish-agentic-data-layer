# Roadmap: built vs planned

The roadmap follows the phases agreed at the start: retail first, then the other three domains reuse
the same core. "Built" means it runs offline and is tested. "Written, not run" means the code or
infrastructure exists and is tested offline (fake clients, Terraform mocks) but has never touched a
real cloud account. "Planned" means only the design exists.

```mermaid
flowchart LR
  P0[Phase 0 value case] --> P1[Phase 1 data] --> P2[Phase 2 insight] --> P3[Phase 3 safe agent access] --> P4[Phase 4 action and value] --> P5[Phase 5 multi-cloud] --> P6[Phase 6 operating model]
```

| Phase | Retail (Wrenfield Grocers) | Status |
|---|---|---|
| 0. Value case | `config/value-case.yaml`, KPI baselines measured from gold (`adl value-case`) | Built |
| 1. Data | 14 feeds, 25 contracts, bronze/silver/gold on Delta Lake, quarantine, PII split, lineage | Built |
| 2. Insight | Forecast, stockout risk, elasticity, markdown, knowledge graph and hybrid retrieval | Built |
| 3. Safe agent access | Data gateway, MCP server, A2A endpoint, attack suite, audit chain | Built (not hosted) |
| 4. Action and value | Agent Framework workflow with approval, value ledger, cost per outcome, FOCUS export | Built (dry-run executor) |
| 5. Multi-cloud | Storage adapters, Terraform for Azure/GCP/AWS, Bicep, OIDC workflows | Written, not run |
| 6. Operating model | RACI, governance, acceptable use, business-model mapping | Built (documents) |

## Next domains

| Order | Domain | Fictional organisation | First use case | Decision | Status |
|---|---|---|---|---|---|
| 1 | Mortgage | Quillmere Home Loans | Rate-lock fallout | Which locks get outreach, a document chase or a lock extension | **Built** ([docs/mortgage](mortgage/README.md)) |
| 2 | Insurance | Ferrowind Insurance | Claims triage and leakage | Queue assignment, leakage review, subrogation referral | Planned |
| 3 | Healthcare | Halsey Vale Health | Inpatient fall risk | Bed alarm, hourly rounding, mobility support | Planned |

Mortgage was built second because its decision (who to call today) has the same shape as retail
(what to order today): a risk score per item, a small set of levers, a person approving the expensive
ones, and a value ledger. Building it moved the approval workflow and the cost export into the shared
core (`adl.core.agentflow`, `adl.core.finops`) and made the gateway's row-scope table a parameter.
Insurance and healthcare are still planned: each has its use case, KPIs and levers in
`src/adl/core/domain.py` and three validated contracts in `domains/<name>/contracts/`; the plan for
each is in its `domains/<name>/README.md`.

## Mortgage items still planned

| Item | Why it is not built |
|---|---|
| MCP and A2A serving for mortgage | The gateway is in-process; serving reuses `adl.serve` once a second domain needs it remotely |
| Live executor (origination system, dialler) | No real system to call; dry run keeps the demo safe |
| Fair-lending outcome testing | The synthetic data has no protected attributes to test with; needs a design with compliance |
| Missed KPI targets (pull-through, extension cost, cycle time) | Reported as missed; closing them needs levers that speed processing, not only reorder attention |

## Retail items still planned

| Item | Why it is not built |
|---|---|
| Live executor (ERP purchase orders, shelf-edge prices) | No real system to call; dry run keeps the demo safe |
| Microsoft Purview registration of contracts and lineage | Needs a tenant; the mapping is in [purview-mapping.md](purview-mapping.md) |
| Calibrated stockout probabilities | The model ranks well but over-estimates; isotonic calibration is the next step |
| Customer-loss model in the simulator | Would let the tuning value lost sales directly (see [ADR 0004](adr/0004-policy-tuning.md)) |
| Online evaluation against real outcomes | Needs a real store; the value ledger is designed to receive those numbers |

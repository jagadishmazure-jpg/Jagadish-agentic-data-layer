# Healthcare domain: Halsey Vale Health (planned)

Status: **planned, not built**. Halsey Vale Health is fictional and no real patient data will ever be
used; the plan is a synthetic ward simulator.

| File | What it does |
|---|---|
| `contracts/` | Three planned contracts: the restricted encounters table and two agent-exposed products |

## The decision

Which patients the nurse in charge should check first on each shift, to prevent inpatient falls. This
is the example the MIT Sloan article uses to illustrate the five steps from data to money, so this
domain will show the same path in a second industry.

| KPI | Direction |
|---|---|
| Falls per 1,000 bed-days | down |
| Falls with harm | down |
| Time to intervention | down |

Levers: bed alarm, hourly rounding, mobility support.

## Plan

```mermaid
flowchart LR
  EHR[synthetic encounters and observations] --> S[silver.inpatient_encounters, restricted]
  S --> W[gold.fall_risk_worklist]
  S --> R[gold.ward_fall_rates]
  W --> AG[assistant proposes interventions]
  AG --> N[nurse in charge decides every action]
  N --> DR[dry run]
```

Every intervention is approved by a nurse (no automatic actions). Insurance eligibility and employee
performance management are prohibited purposes in the contracts. Planned third.

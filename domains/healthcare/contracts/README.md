# domains/healthcare/contracts

Built contracts for the fictional Halsey Vale Health: 9 silver tables and 6 gold data products, all on
**fully synthetic, PHI-free data**. `adl contracts` validates them and the pipeline enforces them
(types, nulls, uniqueness, accepted values, ranges, references and quarantine rules). Patient name,
record number, phone and e-mail exist only in the restricted `silver.patients`; the synthetic patient
group exists only in the restricted `silver.demographics`. No gold product carries either, except
`fairness_monitor`, which holds aggregate rates per group and is granted to one equity identity. Gold
products identify patients only by a salted pseudonym (`PT-`). Every other gold contract prohibits
insurance eligibility, employee performance management, medication or diagnosis decisions, sale of
data and marketing; the value ledger (no patient rows) prohibits sale of data and staff performance
management.

| File | What it does |
|---|---|
| `gold.fairness_monitor.yaml` | Preventive measures and outcomes by synthetic group over the history (current rules): the share of patients given each measure, the share of falls with a measure in place, falls per 1,000 bed-days, and each rate's ratio to the reference group. Aggregates only, cells of at least 11. (confidential, agent-exposed, built) |
| `gold.fall_risk_worklist.yaml` | Every patient in hospital on the planning morning with the probability of a fall in the next three days (logistic model in adl.domains.healthcare.models), its largest driver, the nursing signals the measures are matched to and yesterday's measures. Pseudonymous keys only: no name, record number, encounter id, diagnosis or synthetic group. Decision support for a nurse, not a medical device. (confidential, agent-exposed, built) |
| `gold.nursing_notes.yaml` | Redacted nursing notes keyed by the pseudonymous encounter key, with ward and region; encounter ids in the text are replaced by the key. Returned to agents quoted as untrusted data. (internal, agent-exposed, built) |
| `gold.value_ledger.yaml` | Simulated value of each measure against the current rules over 28 days, with paired 95% bootstrap intervals. (internal, agent-exposed, built) |
| `gold.ward_daily.yaml` | Per ward and day: bed-days, admissions, falls, falls with harm, falls with a measure in place, measure days, sitter shifts, first measures and the days from admission to the first measure, and the cost of falls and measures at the stated assumptions. The source of the healthcare KPIs. Aggregates only, no patient keys. (internal, agent-exposed, built) |
| `gold.ward_fall_rates.yaml` | Falls and falls with harm per 1,000 bed-days per ward and week. Aggregates only, no patient keys. (internal, agent-exposed, built) |
| `silver.demographics.yaml` | Synthetic group (G1 or G2) per record number. Restricted: read only by the fairness audit, never a model feature and never exposed to agents. (restricted, built) |
| `silver.encounters.yaml` | Inpatient stays: record number, ward, bed, admission and discharge day, admission source, age band and a fall in the last three months. Interface resends are removed; training-environment patients and pre-admission records dated after the extract are quarantined. (confidential, built) |
| `silver.fall_incidents.yaml` | Inpatient falls from the incident reporting tool, with whether the fall caused harm and its severity. (confidential, built) |
| `silver.interventions.yaml` | Preventive measures in place per patient and day (bed alarm, hourly rounding, sitter) and mobility aids given. (confidential, built) |
| `silver.morse_assessments.yaml` | Morse fall scale items and total, taken on admission and every third day; assessments whose keyed total does not match the items are quarantined. (confidential, built) |
| `silver.nursing_notes.yaml` | Free-text nursing notes with patient names, record numbers and phone numbers masked and instruction-like text flagged. (confidential, built) |
| `silver.nursing_observations.yaml` | Daily nursing observations per patient: confusion (yes, no, or null when not documented), night restlessness, toileting calls, unsteady gait, and the medication record's sedation flag (a flag only, no drug names). Resent batches are removed; orphans and negative counts are quarantined. (confidential, built) |
| `silver.patients.yaml` | Synthetic patient identity per record number (name, age, phone, e-mail). Restricted: never exposed to agents and never a model feature; used only to redact notes. Every value is invented (no PHI). (restricted, built) |
| `silver.wards.yaml` | Inpatient wards and the region (site) each belongs to; the gateway uses it for row-level security. (internal, built) |

# domains/insurance/contracts

Built contracts for the fictional Ferrowind Insurance: 11 silver tables and 7 gold data products.
`adl contracts` validates them and the pipeline enforces them (types, nulls, uniqueness, accepted values,
ranges, references and quarantine rules). Claimant name, e-mail and phone exist only in the restricted
`silver.claims`; the synthetic postcode proxy group exists only in the restricted
`silver.postcode_groups`. No gold product carries either, except `fairness_monitor`, which holds
aggregate rates per group and is granted to one compliance identity.

| File | What it does |
|---|---|
| `gold.claim_notes.yaml` | Redacted adjuster and intake notes with office and region; returned to agents quoted as untrusted data. (internal, agent-exposed, built) |
| `gold.claims_daily.yaml` | Claims per day, region, line and first queue - new, paid, cycle days, reopenings, escalations, reviews, referrals and the random-audit findings - the source of the claims KPIs. (internal, agent-exposed, built) |
| `gold.claims_triage.yaml` | Every claim reported yesterday and waiting for a queue: the probability it is complex (logistic model in adl.domains.insurance.models, first-notice facts only), its largest driver, the queue the current rules would choose and the queue the policy suggests. No claimant identity, postcode or proxy group. (confidential, agent-exposed, built) |
| `gold.fairness_monitor.yaml` | Claim decisions and outcomes by synthetic proxy group over the history (current rules): selection rates for fast track, leakage review and subrogation referral, mean cycle days, and each rate's ratio to the reference group. Aggregates only. (confidential, agent-exposed, built) |
| `gold.leakage_signals.yaml` | Every claim whose work finished yesterday and is waiting for payment: the probability the proposed payment contains an overpayment and the expected amount, and the probability a third party can be recovered from and the expected recovery (logistic models trained on random audits). No claimant identity, postcode or proxy group. (confidential, agent-exposed, built) |
| `gold.value_ledger.yaml` | Simulated value of each lever against the current rules over 28 days, with paired 95% bootstrap intervals. (internal, agent-exposed, built) |
| `gold.workload_daily.yaml` | Work ready in each queue at the end of each day, in days of the queue's capacity, from the workflow tool's snapshot; the spread is the gap between the fullest and the emptiest queue. (internal, agent-exposed, built) |
| `silver.audits.yaml` | Random closed-file audits after payment: the overpayment found in the proposed payment and whether a recovery from a third party was possible. The unbiased record the leakage models learn from. (confidential, built) |
| `silver.claim_events.yaml` | Claim lifecycle events (reported, assessed with the adjuster's complexity code, ready for payment, paid, reopened, closed); orphan events are quarantined. (confidential, built) |
| `silver.claim_notes.yaml` | Free-text adjuster and intake notes with claimant names, e-mail addresses and phone numbers masked and instruction-like text flagged. (confidential, built) |
| `silver.claims.yaml` | First notice of loss per claim with the claimant's contact details and postcode district; restricted, never exposed to agents. App retries are removed; training-environment records and claims reported before the loss are quarantined. (restricted, built) |
| `silver.offices.yaml` | Claims offices and the region each belongs to; the gateway uses it for row-level security. (internal, built) |
| `silver.payment_requests.yaml` | Proposed payments when a claim's work is finished, with the number of invoices and whether the repairer is in the approved network; negative amounts are quarantined. (confidential, built) |
| `silver.payments.yaml` | Payments made, whether a leakage review was done before payment and the overpayment it removed. (confidential, built) |
| `silver.policies.yaml` | Policies behind the claims: line, tenure and prior claims in the last three years. (confidential, built) |
| `silver.postcode_groups.yaml` | Synthetic proxy group (G1 or G2) for each postcode district. Restricted: read only by the fairness audit, never a model feature and never exposed to agents. (restricted, built) |
| `silver.queue_assignments.yaml` | Queue assignments per claim: triage at first notice, escalations from fast track and reopenings. (confidential, built) |
| `silver.subrogation.yaml` | Subrogation referrals made at payment and what was recovered. (confidential, built) |

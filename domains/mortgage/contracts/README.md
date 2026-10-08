# domains/mortgage/contracts

Built contracts for the fictional Quillmere Home Loans: 10 silver tables and 5 gold data products.
`adl contracts` validates them and the pipeline enforces them (types, nulls, uniqueness, accepted values,
ranges, references and quarantine rules). Applicant name, e-mail and phone exist only in the restricted
`silver.applications`; no gold product carries them.

| File | What it does |
|---|---|
| `gold.fallout_risk.yaml` | Probability each active lock falls out within 14 days, from the logistic model in adl.domains.mortgage.risk, with its largest driver and the value at risk; uses process signals only, no credit or protected attributes (confidential, agent-exposed, built) |
| `gold.lock_position.yaml` | Every active rate lock on the as-of morning - days to expiry, stage, outstanding documents, contact recency and unanswered calls; no borrower identity (confidential, agent-exposed, built) |
| `gold.pipeline_daily.yaml` | Locked pipeline per day, region, product and channel - new locks, closings, fallout by reason, extensions and their cost - the source of the pipeline KPIs (internal, agent-exposed, built) |
| `gold.pipeline_notes.yaml` | Redacted pipeline notes with branch and region; returned to agents quoted as untrusted data (internal, agent-exposed, built) |
| `gold.value_ledger.yaml` | Simulated value of each lever against the current rules over 28 days, with paired 95% bootstrap intervals (internal, agent-exposed, built) |
| `silver.applications.yaml` | Loan applications from the origination system with the applicant's contact details; restricted, never exposed to agents. Sandbox test records are quarantined (restricted, built) |
| `silver.branches.yaml` | Branches and the region each belongs to; the gateway uses it for row-level security (internal, built) |
| `silver.conditions.yaml` | Document conditions per application with the day each was opened and cleared (null while outstanding) (confidential, built) |
| `silver.contacts.yaml` | Outbound calls (reached or not) and document chases per application from the dialler; rows with an unknown outcome are quarantined (confidential, built) |
| `silver.loan_officers.yaml` | Loan officer codes and their branch (no names are needed by any product) (internal, built) |
| `silver.market_rates.yaml` | Daily 30-year market mortgage rate from the market-data feed (synthetic) (internal, built) |
| `silver.pipeline_notes.yaml` | Free-text notes by processors and loan officers with borrower names, e-mail addresses and phone numbers masked and instruction-like text flagged (confidential, built) |
| `silver.products.yaml` | Loan products with the expected gain on sale and the rate spread over the market rate (internal, built) |
| `silver.rate_locks.yaml` | Rate-lock events per application (lock, extension, relock) with the expiry day, the locked rate and the extension fee (confidential, built) |
| `silver.stage_events.yaml` | Pipeline stage changes and final outcomes per application after removing the resent batch; orphan events are quarantined (confidential, built) |

# Changelog

All notable changes to this project are listed here.

## Unreleased

- Retail domain for the fictional Wrenfield Grocers: simulated grocer with ground truth, bronze/silver/gold pipeline with data contracts, quality checks, quarantine, PII split and OpenLineage events.
- Demand forecast, sell-out risk and markdown models, each backtested against a simple baseline.
- Data gateway with agent identities, purposes, row and column security, typed filters and a hash-chained audit log; 14-attempt attack suite.
- Hybrid knowledge retrieval (vector plus knowledge graph) with region trimming and injection screening.
- Agent Framework workflow: code plans, the model narrates, policy checks, approval bound to an action digest, dry-run execution.
- Value case, value ledger with bootstrap intervals, KPI results, cost per outcome and FOCUS cost rows.
- MCP server and A2A endpoint over the gateway.
- Storage adapters for OneLake, Databricks, BigQuery and AWS (written, not run) behind one interface.
- Terraform for Azure, Google Cloud and AWS, Bicep for Azure, hardened workflows; validated in CI, never applied.
- Release gate and full documentation set.
- Mortgage domain for the fictional Quillmere Home Loans (built, synthetic): daily lock-pipeline simulator with hidden borrower traits, 10 bronze feeds with planted faults, 15 built contracts, quality, quarantine, lineage and metrics layer.
- Mortgage fallout-risk model from process signals only, backtested against the "within 10 days of expiry" rule; knowledge corpus with a 24-question retrieval eval.
- Mortgage pipeline assistant (calls, document chases, lock extensions) with digest-bound approval above a $400 fee and dry-run execution; region row security and denied columns; 14-attempt attack suite.
- Mortgage value ledger from a paired 30-replication forward simulation with intervals, KPI results (1 of 4 targets met, misses reported), cost per outcome and FOCUS rows; 11 mortgage gate checks, so `adl gate` runs 26.
- Shared core: domain-neutral approval workflow (`adl.core.agentflow`), FOCUS cost module (`adl.core.finops`) and a configurable gateway row-scope table; retail behaviour unchanged.
- Insurance domain for the fictional Ferrowind Insurance (built, synthetic): daily claims simulator with hidden claim traits and a synthetic postcode proxy group, 12 bronze feeds with planted faults, 18 built contracts (claims and proxy groups restricted), quality, quarantine, lineage and a metrics layer with audit-scaled leakage.
- Insurance value case and fairness limits committed before the simulator, the models or any result.
- Insurance claim models (complexity at first notice; leakage and subrogation learned from random audits), backtested against today's rules; triage thresholds chosen on separate tuning seeds; shared logistic model `adl.core.logit`.
- Insurance claims assistant on the shared approval workflow (queue assignment, leakage review, subrogation referral; no deny action), digest-bound team-lead approval, dry run, five gateway identities, 14-attempt attack suite, 24-document knowledge corpus with a 24-question eval.
- Insurance value ledger from a paired 30-replication forward simulation with intervals, KPI results (1 of 4 targets met, misses reported), cost per outcome and FOCUS rows; unfair-discrimination screen across synthetic proxy groups (a screening heuristic, not a legal test); 12 insurance gate checks, so `adl gate` runs 38.
- Healthcare domain for the fictional Halsey Vale Health (built, fully synthetic and PHI-free): two-site, twelve-ward simulator with hidden patient traits, fall hazards by mechanism and a confusion-documentation gap between synthetic groups; 9 bronze feeds with planted faults; 15 built contracts (patients and demographics restricted); quality, quarantine, note redaction, salted pseudonyms, lineage and a metrics layer.
- Healthcare value case and fairness limits committed before the simulator, the model or any result.
- Healthcare three-day fall-risk model (not a medical device), backtested against the Morse total and the "Morse 45 or more" rule, with model-risk notes and a disclosed one-time world calibration done before any policy run.
- Healthcare fall-prevention assistant on the shared approval workflow: four nursing measures only (bed alarm, hourly rounding, mobility aid, sitter request), no clinical action kind, every measure approved by the nurse in charge against a digest, dry run; six minimum-necessary gateway identities with site rows, denied columns and masked patient keys; purpose limits (no insurance eligibility, staff performance, medication or diagnosis decisions); 14-attempt attack suite; 24-document knowledge corpus with a 24-question eval.
- Healthcare value ledger from a paired 30-replication forward simulation with intervals, per-measure attribution and a sitters-as-today variant; KPI results (1 of 4 targets met, misses reported); cost per outcome and FOCUS rows; fairness check across synthetic patient groups (a screening heuristic); 13 healthcare gate checks, so `adl gate` runs 51.
- Shared core: optional per-identity column masking in the data gateway (`mask_columns`, `mask_token`, a `column_masked` denial and masked columns recorded in the audit); no identity in the other domains uses it.
- README and docs show all four domains complete, with a cross-domain summary table (model vs baseline, net value, KPIs met).

"""Bronze -> silver -> gold for the insurance domain, contract-enforced, with lineage for every job.

The shape is the retail and mortgage one: bronze lands each feed unchanged; silver types and conforms
with SQL, removes app retries of the same first notice and quarantines every row that fails its
contract's row-level checks (including events for claims that were themselves quarantined); gold is
built only from silver.

Claimant names, e-mail addresses, phone numbers and postcode districts stay in the restricted
`silver.claims` table, and the proxy group in the restricted `silver.postcode_groups`. Adjuster notes
are redacted (pattern masking plus the known claimant names) and screened for instruction-like text.
`observe` rebuilds, from silver alone, exactly what the simulator's policies see on a morning: the
claims waiting for a queue and the claims waiting for payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa

from adl.core import guardrails
from adl.core.contracts import Contract, row_predicate
from adl.core.lineage import Lineage
from adl.core.quality import ProductQuality, evaluate
from adl.domains.insurance import world as W

AS_OF = W.DAYS_HISTORY - 1  # last day of data; agents plan for the next morning (day 180)
SOURCES = {
    "fnol": "claims-system",
    "policies": "policy-admin",
    "offices": "office-master",
    "postcode_groups": "postcode-reference",
    "queue_assignments": "claims-system",
    "claim_events": "claims-system",
    "payment_requests": "payments-platform",
    "payments": "payments-platform",
    "audits": "file-audit-tool",
    "subrogation": "subrogation-unit",
    "adjuster_notes": "claims-system",
    "queue_snapshots": "workflow-tool",
}
SILVER_SQL = {
    "offices": "SELECT office_id, name, region FROM bronze.offices",
    "postcode_groups": "SELECT postcode_district, proxy_group FROM bronze.postcode_groups",
    "policies": "SELECT policy_id, line, CAST(tenure_years AS BIGINT) AS tenure_years, CAST(prior_claims_3y AS BIGINT) AS prior_claims_3y FROM bronze.policies",
    "claims": """
        SELECT claim_id, policy_id, claimant_name, email, phone, postcode_district, office_id, line, cause, channel,
               CAST(loss_day AS BIGINT) AS loss_day, CAST(reported_day AS BIGINT) AS reported_day,
               CAST(reported_day - loss_day AS BIGINT) AS report_lag_days, estimate_usd, injury, police_report, third_party_flag, photos
        FROM (SELECT *, row_number() OVER (PARTITION BY claim_id ORDER BY ingest_seq) AS rn FROM bronze.fnol) WHERE rn = 1""",
    "queue_assignments": "SELECT claim_id, CAST(day AS BIGINT) AS day, queue, reason FROM bronze.queue_assignments",
    "claim_events": "SELECT claim_id, CAST(day AS BIGINT) AS day, event, detail FROM bronze.claim_events",
    "payment_requests": "SELECT request_id, claim_id, CAST(day AS BIGINT) AS day, proposed_usd, CAST(invoices AS BIGINT) AS invoices, network_repairer FROM bronze.payment_requests",
    "payments": "SELECT payment_id, claim_id, CAST(day AS BIGINT) AS day, proposed_usd, paid_usd, reviewed, overpayment_found_usd FROM bronze.payments",
    "audits": "SELECT audit_id, claim_id, CAST(day AS BIGINT) AS day, overpayment_found_usd, subrogation_potential FROM bronze.audits",
    "subrogation": "SELECT claim_id, CAST(referral_day AS BIGINT) AS referral_day, recovered_usd, status FROM bronze.subrogation",
}
MISSED = W.RECOVERY_SHARE * W.RECOVERY_SUCCESS  # expected recovery per dollar paid when a recoverable claim is not referred
GOLD_SQL = {
    "claims_daily": """
        WITH c AS (SELECT c.claim_id, o.region, c.line, c.reported_day, coalesce(q.queue, 'standard') AS queue
                   FROM silver.claims c JOIN silver.offices o USING (office_id)
                   LEFT JOIN silver.queue_assignments q ON q.claim_id = c.claim_id AND q.reason = 'triage'),
        sub AS (SELECT claim_id FROM silver.subrogation),
        ev AS (
          SELECT q.day, c.region, c.line, c.queue, 1 AS new_claims, 0 AS paid, 0 AS cyc, 0 AS reo, 0 AS esc, 0 AS rev, 0.0 AS over, 0 AS refs,
                 0.0 AS rec, 0.0 AS paid_usd, 0 AS aud, 0.0 AS aud_over, 0.0 AS aud_miss
          FROM silver.queue_assignments q JOIN c USING (claim_id) WHERE q.reason = 'triage'
          UNION ALL
          SELECT q.day, c.region, c.line, c.queue, 0, 0, 0, 0, 1, 0, 0.0, 0, 0.0, 0.0, 0, 0.0, 0.0
          FROM silver.queue_assignments q JOIN c USING (claim_id) WHERE q.reason = 'escalation'
          UNION ALL
          SELECT e.day, c.region, c.line, c.queue, 0, 0, 0, 1, 0, 0, 0.0, 0, 0.0, 0.0, 0, 0.0, 0.0
          FROM silver.claim_events e JOIN c USING (claim_id) WHERE e.event = 'reopened'
          UNION ALL
          SELECT p.day, c.region, c.line, c.queue, 0, 1, p.day - c.reported_day, 0, 0, CAST(p.reviewed AS INT), p.overpayment_found_usd, 0, 0.0,
                 p.paid_usd, 0, 0.0, 0.0
          FROM silver.payments p JOIN c USING (claim_id)
          UNION ALL
          SELECT s.referral_day, c.region, c.line, c.queue, 0, 0, 0, 0, 0, 0, 0.0, 1, s.recovered_usd, 0.0, 0, 0.0, 0.0
          FROM silver.subrogation s JOIN c USING (claim_id)
          UNION ALL
          SELECT a.day, c.region, c.line, c.queue, 0, 0, 0, 0, 0, 0, 0.0, 0, 0.0, 0.0, 1, a.overpayment_found_usd,
                 CASE WHEN a.subrogation_potential AND a.claim_id NOT IN (SELECT claim_id FROM sub) THEN {missed} * p.paid_usd ELSE 0 END
          FROM silver.audits a JOIN c USING (claim_id) JOIN silver.payments p USING (claim_id)),
        grid AS (SELECT d.range AS day, r.region, l.line, q.queue
                 FROM range(0, {days}) d, (SELECT DISTINCT region FROM silver.offices) r,
                      (SELECT unnest(['auto', 'home', 'commercial']) AS line) l, (SELECT unnest(['fast_track', 'standard', 'complex']) AS queue) q)
        SELECT CAST(g.day AS BIGINT) AS day, g.region, g.line, g.queue,
               CAST(coalesce(sum(new_claims), 0) AS BIGINT) AS new_claims, CAST(coalesce(sum(paid), 0) AS BIGINT) AS paid_claims,
               CAST(coalesce(sum(cyc), 0) AS BIGINT) AS cycle_days, CAST(coalesce(sum(reo), 0) AS BIGINT) AS reopened,
               CAST(coalesce(sum(esc), 0) AS BIGINT) AS escalations, CAST(coalesce(sum(rev), 0) AS BIGINT) AS reviews,
               round(coalesce(sum(over), 0), 2) AS overpayment_found_usd, CAST(coalesce(sum(refs), 0) AS BIGINT) AS referrals,
               round(coalesce(sum(rec), 0), 2) AS recovered_usd, round(coalesce(sum(paid_usd), 0), 2) AS paid_usd,
               CAST(coalesce(sum(aud), 0) AS BIGINT) AS audited_claims, round(coalesce(sum(aud_over), 0), 2) AS audit_overpayment_usd,
               round(coalesce(sum(aud_miss), 0), 2) AS audit_missed_recovery_usd
        FROM grid g LEFT JOIN ev USING (day, region, line, queue) GROUP BY ALL""",
    "workload_daily": """
        SELECT CAST(day AS BIGINT) AS day,
               max(ready_work_days) FILTER (WHERE queue = 'fast_track') AS fast_track_days,
               max(ready_work_days) FILTER (WHERE queue = 'standard') AS standard_days,
               max(ready_work_days) FILTER (WHERE queue = 'complex') AS complex_days,
               round(max(ready_work_days) - min(ready_work_days), 3) AS spread_days
        FROM bronze.queue_snapshots GROUP BY day""",
    "claim_notes": """
        SELECT n.note_id, n.claim_id, n.office_id, o.region, n.day, n.author_role, n.text_redacted, n.injection_flag
        FROM silver.claim_notes n JOIN silver.offices o USING (office_id)""",
    "fairness_monitor": """
        WITH c AS (SELECT c.claim_id, c.reported_day, g.proxy_group FROM silver.claims c JOIN silver.postcode_groups g USING (postcode_district)),
        d AS (
          SELECT 'fast_track' AS decision, c.proxy_group, count(*) AS eligible, count(*) FILTER (WHERE q.queue = 'fast_track') AS selected, NULL AS cyc
          FROM silver.queue_assignments q JOIN c USING (claim_id) WHERE q.reason = 'triage' GROUP BY ALL
          UNION ALL
          SELECT 'leakage_review', c.proxy_group, count(*), count(*) FILTER (WHERE p.reviewed), NULL
          FROM silver.payments p JOIN c USING (claim_id) GROUP BY ALL
          UNION ALL
          SELECT 'subrogation_referral', c.proxy_group, count(*), count(s.claim_id), NULL
          FROM silver.payments p JOIN c USING (claim_id) LEFT JOIN silver.subrogation s USING (claim_id) GROUP BY ALL
          UNION ALL
          SELECT 'cycle_days', c.proxy_group, count(*), count(*), avg(p.day - c.reported_day)
          FROM silver.payments p JOIN c USING (claim_id) GROUP BY ALL),
        r AS (SELECT decision, proxy_group AS group_name, CAST(eligible AS BIGINT) AS eligible, CAST(selected AS BIGINT) AS selected,
                     CASE WHEN decision = 'cycle_days' THEN cyc ELSE selected / eligible END AS rate FROM d)
        SELECT r.decision, r.group_name, r.eligible, r.selected, round(r.rate, 6) AS rate,
               round(r.rate / nullif(ref.rate, 0), 4) AS ratio_to_reference
        FROM r JOIN r AS ref ON ref.decision = r.decision AND ref.group_name = 'G1' ORDER BY 1, 2""",
}
OBSERVE_SQL = """
    WITH st AS (
           SELECT claim_id, arg_max(state, day) AS state FROM (
             SELECT claim_id, day, 'open' AS state FROM silver.queue_assignments WHERE reason IN ('triage', 'reopen') AND day <= $t - 1
             UNION ALL
             SELECT claim_id, day, CASE event WHEN 'ready' THEN 'pending' ELSE 'closed' END FROM silver.claim_events
             WHERE event IN ('ready', 'paid', 'closed') AND day <= $t - 1)
           GROUP BY claim_id),
         q AS (SELECT claim_id, arg_max(queue, day) AS queue, bool_or(reason = 'escalation') AS escalated
               FROM silver.queue_assignments WHERE day <= $t - 1 GROUP BY claim_id),
         pr AS (SELECT claim_id, arg_max(proposed_usd, day) AS proposed, arg_max(invoices, day) AS invoices, arg_max(network_repairer, day) AS network
                FROM silver.payment_requests WHERE day <= $t - 1 GROUP BY claim_id),
         base AS (SELECT c.claim_id, c.office_id, o.region, c.line, c.cause, c.channel, c.estimate_usd, c.injury, c.police_report,
                         c.third_party_flag, c.photos, c.report_lag_days, c.reported_day, p.tenure_years, p.prior_claims_3y
                  FROM silver.claims c JOIN silver.offices o USING (office_id) JOIN silver.policies p USING (policy_id)
                  WHERE c.reported_day <= $t - 1)
    SELECT base.*, coalesce(st.state, 'new') AS state, q.queue, coalesce(q.escalated, false) AS escalated, pr.proposed, pr.invoices, pr.network
    FROM base LEFT JOIN st USING (claim_id) LEFT JOIN q USING (claim_id) LEFT JOIN pr USING (claim_id)
    WHERE coalesce(st.state, 'new') IN ('new', 'pending', 'open')
    ORDER BY claim_id"""


@dataclass
class Observed:
    view: W.View
    new_rows: list[dict]
    pay_rows: list[dict]


def observe(store, t: int) -> Observed:
    """The claims waiting for a queue and for payment on the morning of day t, from silver only."""
    rows = store.sql(OBSERVE_SQL, {"t": t})
    new = [r for r in rows if r["state"] == "new"]
    pay = [r for r in rows if r["state"] == "pending"]
    both = new + pay
    col = lambda rs, k, dt=int: np.array([r[k] for r in rs], dt)  # noqa: E731
    idx = lambda rs: np.array([int(r["claim_id"][4:]) - 1 for r in rs], int)  # noqa: E731
    v = W.View(
        t=t,
        new=idx(new),
        pay=idx(pay),
        line=np.array([W.LINES.index(r["line"]) for r in both], int),
        cause=np.array([W.CAUSES.index(r["cause"]) for r in both], int),
        channel=np.array([W.CHANNELS.index(r["channel"]) for r in both], int),
        estimate=col(both, "estimate_usd", float),
        injury=col(both, "injury"),
        police=col(both, "police_report"),
        tp_flag=col(both, "third_party_flag"),
        photos=col(both, "photos"),
        late=col(both, "report_lag_days"),
        tenure=col(both, "tenure_years"),
        prior=col(both, "prior_claims_3y"),
        proposed=col(pay, "proposed", float),
        invoices=col(pay, "invoices"),
        nonnetwork=np.array([0 if r["network"] else 1 for r in pay], int),
        queue=np.array([W.QUEUES.index(r["queue"]) for r in pay], int),
        escalated=col(pay, "escalated"),
        days_open=t - col(pay, "reported_day"),
        open_by_queue=tuple(sum(1 for r in rows if r["state"] == "open" and r["queue"] == q) for q in W.QUEUES),
    )
    return Observed(v, new, pay)


@dataclass
class Build:
    store: object
    contracts: dict[str, Contract]
    lineage: Lineage
    quality: dict[str, ProductQuality] = field(default_factory=dict)
    quarantined: dict[str, int] = field(default_factory=dict)
    bronze_rows: dict[str, int] = field(default_factory=dict)


def _schema(t: pa.Table) -> list[tuple[str, str]]:
    return [(f.name, str(f.type)) for f in t.schema]


def _dq_facet(q: ProductQuality) -> dict:
    return {"assertions": [{"assertion": f"{r.check}:{r.target}", "success": r.passed, "column": r.target} for r in q.results]}


def land_bronze(b: Build, tables: dict[str, pa.Table]) -> None:
    for name, data in tables.items():
        b.store.write("bronze", name, data)
        b.bronze_rows[name] = data.num_rows
        b.lineage.run(f"bronze.{name}", [f"source.{SOURCES[name]}"], [{"name": f"bronze.{name}", "schema": _schema(data), "rows": data.num_rows}])


def _conform(b: Build, table: str, data: pa.Table) -> None:
    c = b.contracts[f"insurance.silver.{table}"]
    s = b.store
    s.stage(f"stage_{table}", data)
    pred = row_predicate(c, b.contracts, s.qualified)
    cols = ", ".join(f'"{x}"' for x in c.columns)
    good = s.arrow(f"SELECT {cols} FROM stage_{table} WHERE {pred}")
    bad = s.arrow(f"SELECT * FROM stage_{table} WHERE NOT ({pred})")
    s.write("silver", table, good)
    if bad.num_rows:
        s.write("silver", f"quarantine_{table}", bad)
    b.quarantined[c.id] = bad.num_rows
    q = evaluate(c, s, b.contracts, bad.num_rows, AS_OF)
    b.quality[c.id] = q
    outs = [{"name": f"silver.{table}", "schema": _schema(good), "rows": good.num_rows, "dq": _dq_facet(q)}]
    if bad.num_rows:
        outs.append({"name": f"silver.quarantine_{table}", "rows": bad.num_rows})
    inputs = [f"{x.split('.')[1]}.{x.split('.')[2]}" for x in c.inputs]
    b.lineage.run(f"silver.{table}", inputs, outs)


def build_silver(b: Build) -> None:
    s = b.store
    for table, sql in SILVER_SQL.items():
        _conform(b, table, s.arrow(sql))
    names = sorted({r["claimant_name"] for r in s.sql("SELECT claimant_name FROM silver.claims")}, key=len, reverse=True)
    notes = []
    for n in s.read("bronze", "adjuster_notes").to_pylist():
        text, masked = guardrails.redact(n["text"])
        for name in names:  # known claimants' names, matched against the restricted claims table
            if name in text:
                text, masked = text.replace(name, "[NAME]"), True
        notes.append(
            {
                "note_id": n["note_id"],
                "claim_id": n["claim_id"],
                "office_id": n["office_id"],
                "day": n["day"],
                "author_role": n["author_role"],
                "text_redacted": text,
                "injection_flag": guardrails.screen(n["text"]).flagged,
                "pii_redacted": masked,
            }
        )
    _conform(b, "claim_notes", pa.Table.from_pylist(notes))


def build_gold(b: Build) -> None:
    for table, sql in GOLD_SQL.items():
        write_gold(b, table, b.store.arrow(sql.format(days=W.DAYS_HISTORY, missed=MISSED)))


def write_gold(b: Build, table: str, data: pa.Table, extra_inputs: list[str] | None = None) -> ProductQuality:
    c = b.contracts[f"insurance.gold.{table}"]
    cols = ", ".join(f'"{x}"' for x in c.columns)
    b.store.write("gold", table, data)
    data = b.store.arrow(f"SELECT {cols} FROM gold.{table}")  # enforce declared column order
    b.store.write("gold", table, data)
    q = evaluate(c, b.store, b.contracts, 0, AS_OF)
    b.quality[c.id] = q
    inputs = [f"{x.split('.')[1]}.{x.split('.')[2]}" for x in c.inputs] + (extra_inputs or [])
    b.lineage.run(f"gold.{table}", inputs, [{"name": f"gold.{table}", "schema": _schema(data), "rows": data.num_rows, "dq": _dq_facet(q)}])
    return q

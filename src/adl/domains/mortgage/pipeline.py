"""Bronze -> silver -> gold for the mortgage domain, contract-enforced, with lineage for every job.

The shape is the retail one: bronze lands each feed unchanged; silver types and conforms with SQL,
removes the resent batch and quarantines every row that fails its contract's row-level checks
(including events for applications that were themselves quarantined); gold is built only from silver.

Borrower names, e-mail addresses and phone numbers stay in the restricted `silver.applications`
table. Pipeline notes are redacted (pattern masking plus the known applicant names) and screened for
instruction-like text before they reach silver. `gold.lock_position` is the point-in-time view of
every active lock on the as-of morning, computed by `observe` - the same function the fallout model
uses for every backtest origin.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa

from adl.core import guardrails
from adl.core.contracts import Contract, row_predicate
from adl.core.lineage import Lineage
from adl.core.quality import ProductQuality, evaluate
from adl.domains.mortgage import world as W

AS_OF = W.DAYS_HISTORY - 1  # last day of data; agents plan for the next morning (day 180)
SOURCES = {
    "los_applications": "loan-origination-system",
    "products": "product-catalogue",
    "branches": "branch-master",
    "loan_officers": "branch-master",
    "rate_locks": "lock-desk",
    "stage_events": "loan-origination-system",
    "conditions": "loan-origination-system",
    "contacts": "dialler",
    "market_rates": "market-data",
    "pipeline_notes": "loan-origination-system",
}
SILVER_SQL = {
    "branches": "SELECT branch_id, name, region FROM bronze.branches",
    "loan_officers": "SELECT lo_id, branch_id FROM bronze.loan_officers",
    "products": "SELECT product, gain_on_sale_pct, rate_spread_pct FROM bronze.products",
    "applications": """
        SELECT application_id, applicant_name, email, phone, branch_id, lo_id, product, channel, purpose, loan_amount_usd, ltv_pct,
               CAST(application_day AS BIGINT) AS application_day, CAST(lock_term_days AS BIGINT) AS lock_term_days
        FROM bronze.los_applications""",
    "rate_locks": """
        SELECT application_id, event, CAST(day AS BIGINT) AS day, CAST(lock_expiry_day AS BIGINT) AS lock_expiry_day, locked_rate_pct, fee_usd
        FROM bronze.rate_locks""",
    "stage_events": """
        SELECT application_id, CAST(day AS BIGINT) AS day, stage
        FROM (SELECT *, row_number() OVER (PARTITION BY application_id, day, stage, batch_id ORDER BY ingest_seq DESC) AS rn FROM bronze.stage_events)
        WHERE rn = 1""",
    "conditions": """
        SELECT condition_id, application_id, condition_type, CAST(opened_day AS BIGINT) AS opened_day, CAST(cleared_day AS BIGINT) AS cleared_day
        FROM bronze.conditions""",
    "contacts": "SELECT contact_id, application_id, lo_id, CAST(day AS BIGINT) AS day, kind, outcome FROM bronze.contacts",
    "market_rates": "SELECT CAST(day AS BIGINT) AS day, rate_30y_pct FROM bronze.market_rates",
}
GOLD_SQL = {
    "pipeline_daily": """
        WITH a AS (SELECT a.application_id, b.region, a.product, a.channel, a.loan_amount_usd, p.gain_on_sale_pct
                   FROM silver.applications a JOIN silver.branches b USING (branch_id) JOIN silver.products p USING (product)),
        first_lock AS (SELECT application_id, min(day) AS lock_day FROM silver.rate_locks WHERE event = 'lock' GROUP BY ALL),
        ev AS (
          SELECT l.day, a.region, a.product, a.channel, CAST(l.event = 'lock' AS INT) AS new_locks, 0 AS closed, 0 AS withdrawn, 0 AS expired,
                 0 AS denied, CAST(l.event = 'extension' AS INT) AS extensions, CASE WHEN l.event = 'extension' THEN l.fee_usd ELSE 0 END AS ext_cost,
                 0 AS l2c, 0.0 AS gos
          FROM silver.rate_locks l JOIN a USING (application_id)
          UNION ALL
          SELECT s.day, a.region, a.product, a.channel, 0, CAST(s.stage = 'closed' AS INT), CAST(s.stage = 'withdrawn' AS INT),
                 CAST(s.stage = 'expired' AS INT), CAST(s.stage = 'denied' AS INT), 0, 0.0,
                 CASE WHEN s.stage = 'closed' THEN s.day - f.lock_day ELSE 0 END,
                 CASE WHEN s.stage = 'closed' THEN a.loan_amount_usd * a.gain_on_sale_pct / 100 ELSE 0 END
          FROM silver.stage_events s JOIN a USING (application_id) LEFT JOIN first_lock f USING (application_id)
          WHERE s.stage IN ('closed', 'withdrawn', 'expired', 'denied')),
        grid AS (SELECT d.range AS day, r.region, p.product, c.channel
                 FROM range(0, {days}) d, (SELECT DISTINCT region FROM silver.branches) r, silver.products p,
                      (SELECT DISTINCT channel FROM silver.applications) c)
        SELECT CAST(g.day AS BIGINT) AS day, g.region, g.product, g.channel,
               CAST(coalesce(sum(new_locks), 0) AS BIGINT) AS new_locks, CAST(coalesce(sum(closed), 0) AS BIGINT) AS closed,
               CAST(coalesce(sum(withdrawn), 0) AS BIGINT) AS withdrawn, CAST(coalesce(sum(expired), 0) AS BIGINT) AS expired,
               CAST(coalesce(sum(denied), 0) AS BIGINT) AS denied,
               CAST(coalesce(sum(withdrawn) + sum(expired) + sum(denied), 0) AS BIGINT) AS fallout,
               CAST(coalesce(sum(extensions), 0) AS BIGINT) AS extensions, round(coalesce(sum(ext_cost), 0), 2) AS extension_cost_usd,
               CAST(coalesce(sum(l2c), 0) AS BIGINT) AS lock_to_close_days, round(coalesce(sum(gos), 0), 2) AS gain_on_sale_usd
        FROM grid g LEFT JOIN ev USING (day, region, product, channel) GROUP BY ALL""",
    "pipeline_notes": """
        SELECT n.note_id, n.application_id, lo.branch_id, b.region, n.lo_id, n.day, n.author_role, n.text_redacted, n.injection_flag
        FROM silver.pipeline_notes n JOIN silver.loan_officers lo USING (lo_id) JOIN silver.branches b ON b.branch_id = lo.branch_id""",
}

OBSERVE_SQL = """
    WITH lk AS (SELECT application_id, min(day) FILTER (WHERE event = 'lock') AS lock_day, arg_max(locked_rate_pct, day) AS locked_rate,
                       arg_max(lock_expiry_day, day) AS expiry, count(*) FILTER (WHERE event = 'extension') AS extensions
                FROM silver.rate_locks WHERE day <= $t - 1 GROUP BY ALL),
         ended AS (SELECT DISTINCT application_id FROM silver.stage_events
                   WHERE day <= $t - 1 AND stage IN ('closed', 'withdrawn', 'expired', 'denied')),
         st AS (SELECT application_id, arg_max(stage, day) AS stage, max(day) AS stage_since FROM silver.stage_events
                WHERE day <= $t - 1 AND stage NOT IN ('closed', 'withdrawn', 'expired', 'denied') GROUP BY ALL),
         cd AS (SELECT application_id, count(*) AS n_cond, count(*) FILTER (WHERE cleared_day IS NULL OR cleared_day > $t - 1) AS docs_out
                FROM silver.conditions WHERE opened_day <= $t - 1 GROUP BY ALL),
         ct AS (SELECT application_id, max(day) FILTER (WHERE kind = 'call' AND outcome = 'reached') AS last_reached,
                       count(*) FILTER (WHERE kind = 'call' AND outcome = 'no_answer' AND day >= $t - 7) AS unanswered
                FROM silver.contacts WHERE day <= $t - 1 GROUP BY ALL)
    SELECT a.application_id, a.branch_id, b.region, a.lo_id, a.product, a.channel, a.purpose, a.loan_amount_usd,
           lk.locked_rate, lk.lock_day, lk.expiry, lk.extensions, st.stage, st.stage_since,
           coalesce(cd.n_cond, 0) AS n_cond, coalesce(cd.docs_out, 0) AS docs_out,
           greatest(lk.lock_day, coalesce(ct.last_reached, lk.lock_day)) AS last_contact, coalesce(ct.unanswered, 0) AS unanswered,
           (SELECT rate_30y_pct FROM silver.market_rates WHERE day = $t - 1) AS market_rate
    FROM lk JOIN silver.applications a USING (application_id) JOIN silver.branches b USING (branch_id) JOIN st USING (application_id)
    LEFT JOIN cd USING (application_id) LEFT JOIN ct USING (application_id)
    WHERE lk.lock_day IS NOT NULL AND application_id NOT IN (SELECT application_id FROM ended)
    ORDER BY application_id"""


@dataclass
class Observed:
    view: W.View
    rows: list[dict]  # the same applications with their identifiers, branch, region and officer


def observe(store, t: int) -> Observed:
    """Every active lock on the morning of day t, from silver only (everything through day t-1)."""
    rows = store.sql(OBSERVE_SQL, {"t": t})
    col = lambda k, dt=int: np.array([r[k] for r in rows], dt)  # noqa: E731
    v = W.View(
        t=t,
        idx=np.array([int(r["application_id"][4:]) - 1 for r in rows], int),
        market_rate=float(rows[0]["market_rate"]) if rows else float("nan"),
        locked_rate=col("locked_rate", float),
        expiry=col("expiry"),
        extensions=col("extensions"),
        docs_out=col("docs_out"),
        n_cond=col("n_cond"),
        stage=np.array([W.STAGES.index(r["stage"]) for r in rows], int),
        stage_since=col("stage_since"),
        last_contact=col("last_contact"),
        unanswered_7d=col("unanswered"),
        lock_day=col("lock_day"),
        channel=np.array([W.CHANNELS.index(r["channel"]) for r in rows], int),
        purpose=np.array([W.PURPOSES.index(r["purpose"]) for r in rows], int),
        product=np.array([W.PRODUCTS.index(r["product"]) for r in rows], int),
        amount=col("loan_amount_usd", float),
    )
    return Observed(v, rows)


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
    c = b.contracts[f"mortgage.silver.{table}"]
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
    names = sorted({r["applicant_name"] for r in s.sql("SELECT applicant_name FROM silver.applications")}, key=len, reverse=True)
    notes = []
    for n in s.read("bronze", "pipeline_notes").to_pylist():
        text, masked = guardrails.redact(n["text"])
        for name in names:  # known applicants' names, matched against the restricted applications table
            if name in text:
                text, masked = text.replace(name, "[NAME]"), True
        notes.append(
            {
                "note_id": n["note_id"],
                "application_id": n["application_id"],
                "lo_id": n["lo_id"],
                "day": n["day"],
                "author_role": n["author_role"],
                "text_redacted": text,
                "injection_flag": guardrails.screen(n["text"]).flagged,
                "pii_redacted": masked,
            }
        )
    _conform(b, "pipeline_notes", pa.Table.from_pylist(notes))


def lock_position(store, t: int = AS_OF + 1) -> pa.Table:
    o = observe(store, t)
    v = o.view
    return pa.Table.from_pylist(
        [
            {
                "application_id": r["application_id"],
                "branch_id": r["branch_id"],
                "region": r["region"],
                "lo_id": r["lo_id"],
                "product": r["product"],
                "channel": r["channel"],
                "purpose": r["purpose"],
                "loan_amount_usd": r["loan_amount_usd"],
                "locked_rate_pct": r["locked_rate"],
                "market_rate_pct": v.market_rate,
                "lock_day": r["lock_day"],
                "lock_expiry_day": r["expiry"],
                "days_to_expiry": r["expiry"] - t,
                "extensions": r["extensions"],
                "stage": r["stage"],
                "days_in_stage": t - r["stage_since"],
                "conditions_total": r["n_cond"],
                "docs_outstanding": r["docs_out"],
                "days_since_contact": t - r["last_contact"],
                "unanswered_calls_7d": r["unanswered"],
                "as_of_day": t - 1,
            }
            for r in o.rows
        ]
    )


def build_gold(b: Build) -> None:
    for table, sql in GOLD_SQL.items():
        write_gold(b, table, b.store.arrow(sql.format(days=W.DAYS_HISTORY)))
    write_gold(b, "lock_position", lock_position(b.store))


def write_gold(b: Build, table: str, data: pa.Table, extra_inputs: list[str] | None = None) -> ProductQuality:
    c = b.contracts[f"mortgage.gold.{table}"]
    cols = ", ".join(f'"{x}"' for x in c.columns)
    b.store.write("gold", table, data)
    data = b.store.arrow(f"SELECT {cols} FROM gold.{table}")  # enforce declared column order
    b.store.write("gold", table, data)
    q = evaluate(c, b.store, b.contracts, 0, AS_OF)
    b.quality[c.id] = q
    inputs = [f"{x.split('.')[1]}.{x.split('.')[2]}" for x in c.inputs] + (extra_inputs or [])
    b.lineage.run(f"gold.{table}", inputs, [{"name": f"gold.{table}", "schema": _schema(data), "rows": data.num_rows, "dq": _dq_facet(q)}])
    return q

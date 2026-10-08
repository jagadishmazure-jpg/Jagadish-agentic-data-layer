"""Bronze -> silver -> gold for the retail domain, contract-enforced, with lineage for every job.

* Bronze: each source table is landed unchanged as a Delta table (schema on read).
* Silver: typed and conformed with SQL; duplicates removed; every row is tested against the row-level
  checks of its contract and failures go to `silver.quarantine_<table>` instead of being dropped
  silently. Loyalty data is split: direct identifiers go to the restricted `loyalty_pii` table and
  everything else is keyed by an HMAC pseudonym. Store notes are redacted and screened here.
* Gold: data products built only from silver, checked against their contracts and SLOs.

Every step emits OpenLineage START/COMPLETE events with schema, row counts and data-quality facets.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field

import pyarrow as pa

from adl.core import guardrails
from adl.core.contracts import Contract, row_predicate
from adl.core.lineage import Lineage
from adl.core.quality import ProductQuality, evaluate
from adl.domains.retail import world as W

AS_OF = W.DAYS_HISTORY - 1  # the morning after day 139 is "today" for the agents
SOURCES = {
    "pos_sales": "pos",
    "inventory_snapshots": "warehouse",
    "purchase_orders": "supplier-edi",
    "products": "merchandising",
    "stores": "store-master",
    "suppliers": "supplier-master",
    "promotions": "pricing",
    "price_changes": "pricing",
    "price_tests": "pricing",
    "weather": "weather-feed",
    "store_events": "store-app",
    "loyalty_customers": "loyalty-crm",
    "loyalty_visits": "loyalty-crm",
    "store_notes": "store-app",
}

SILVER_SQL = {
    "stores": "SELECT store_id, name, region, size_factor FROM bronze.stores",
    "suppliers": "SELECT supplier_id, name, categories, CAST(nominal_lead_days AS BIGINT) AS nominal_lead_days FROM bronze.suppliers",
    "products": "SELECT sku, name, category, list_price, unit_cost, supplier_id, CAST(case_pack AS BIGINT) AS case_pack, CAST(shelf_life_days AS BIGINT) AS shelf_life_days FROM bronze.products",
    "pos_sales": """
        SELECT store_id, sku, CAST(day AS BIGINT) AS day, CAST(units AS BIGINT) AS units, net_sales, shelf_price,
               CAST(markdown_units AS BIGINT) AS markdown_units, markdown_amount, promo_flag
        FROM (SELECT *, row_number() OVER (PARTITION BY store_id, sku, day, batch_id ORDER BY ingest_seq DESC) AS rn FROM bronze.pos_sales)
        WHERE rn = 1""",
    "inventory": "SELECT store_id, sku, CAST(day AS BIGINT) AS day, CAST(on_hand_end AS BIGINT) AS on_hand_end, CAST(waste_units AS BIGINT) AS waste_units, CAST(near_expiry_units AS BIGINT) AS near_expiry_units FROM bronze.inventory_snapshots",
    "purchase_orders": """
        SELECT po_id, store_id, sku, supplier_id, CAST(order_day AS BIGINT) AS order_day, CAST(qty_ordered AS BIGINT) AS qty_ordered,
               CAST(expected_day AS BIGINT) AS expected_day,
               CAST(CASE WHEN received_day <= {as_of} THEN received_day END AS BIGINT) AS received_day,
               CAST(CASE WHEN received_day <= {as_of} THEN qty_received END AS BIGINT) AS qty_received,
               CAST(CASE WHEN received_day <= {as_of} THEN received_day - order_day END AS BIGINT) AS lead_time_days,
               CASE WHEN received_day <= {as_of} THEN received_day <= expected_day END AS on_time,
               CASE WHEN received_day <= {as_of} THEN qty_received / qty_ordered END AS fill_rate
        FROM bronze.purchase_orders""",
    "promotions": "SELECT sku, CAST(start_day AS BIGINT) AS start_day, CAST(end_day AS BIGINT) AS end_day, price_ratio, display FROM bronze.promotions",
    "prices": """
        WITH d AS (SELECT range AS day FROM range(0, {days})),
             g AS (SELECT p.sku, d.day, p.list_price FROM bronze.products p CROSS JOIN d),
             c AS (SELECT g.sku, g.day, g.list_price, max(pc.day) AS last_change FROM g LEFT JOIN bronze.price_changes pc ON pc.sku = g.sku AND pc.day <= g.day GROUP BY ALL)
        SELECT c.sku, CAST(c.day AS BIGINT) AS day, round(c.list_price * coalesce(pc.multiplier, 1.0), 2) AS regular_price
        FROM c LEFT JOIN bronze.price_changes pc ON pc.sku = c.sku AND pc.day = c.last_change""",
    "price_tests": "SELECT store_id, sku, CAST(start_day AS BIGINT) AS start_day, CAST(end_day AS BIGINT) AS end_day, multiplier FROM bronze.price_tests",
    "weather": "SELECT region, CAST(day AS BIGINT) AS day, temp_anomaly_c, rain FROM bronze.weather",
    "store_events": "SELECT store_id, CAST(day AS BIGINT) AS day, event_type FROM bronze.store_events",
}

GOLD_SQL = {
    "sales_daily": """
        WITH rec AS (SELECT store_id, sku, received_day AS day, sum(qty_received) AS received_units FROM silver.purchase_orders
                     WHERE received_day IS NOT NULL GROUP BY ALL),
        base AS (
          SELECT p.store_id, st.region, p.sku, pr.category, p.day, p.units, round(p.net_sales, 2) AS revenue_usd, p.shelf_price,
                 rp.regular_price, p.promo_flag AS promo, p.markdown_units, round(p.markdown_amount, 2) AS markdown_usd,
                 i.waste_units, round(i.waste_units * pr.unit_cost, 2) AS waste_cost_usd, round(p.units * pr.unit_cost, 2) AS cogs_usd,
                 i.on_hand_end, (i.on_hand_end + i.waste_units = 0) AS sold_out, CAST(coalesce(r.received_units, 0) AS BIGINT) AS received_units
          FROM silver.pos_sales p
          JOIN silver.inventory i USING (store_id, sku, day)
          JOIN silver.products pr USING (sku)
          JOIN silver.stores st USING (store_id)
          JOIN silver.prices rp ON rp.sku = p.sku AND rp.day = p.day
          LEFT JOIN rec r ON r.store_id = p.store_id AND r.sku = p.sku AND r.day = p.day),
        ref AS (
          SELECT *, avg(CASE WHEN NOT sold_out THEN units END) OVER (
                     PARTITION BY store_id, sku, day % 7 ORDER BY day ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) AS ref_units
          FROM base)
        SELECT store_id, region, sku, category, day, units, revenue_usd, shelf_price, regular_price, promo, markdown_units, markdown_usd,
               waste_units, waste_cost_usd, cogs_usd, on_hand_end, sold_out,
               round(CASE WHEN sold_out THEN greatest(coalesce(ref_units, units) - units, 0) ELSE 0 END, 3) AS est_lost_units,
               round(CASE WHEN sold_out THEN greatest(coalesce(ref_units, units) - units, 0) * shelf_price ELSE 0 END, 2) AS est_lost_sales_usd,
               received_units
        FROM ref""",
    "inventory_position": """
        WITH oo AS (SELECT store_id, sku, sum(qty_ordered) AS on_order FROM silver.purchase_orders WHERE received_day IS NULL GROUP BY ALL),
             a AS (SELECT store_id, sku, avg(units) AS avg28 FROM silver.pos_sales WHERE day > {as_of} - 28 GROUP BY ALL)
        SELECT i.store_id, st.region, i.sku, pr.category, pr.supplier_id, pr.case_pack, pr.shelf_life_days, pr.unit_cost, pr.list_price, CAST({as_of} AS BIGINT) AS as_of_day, i.on_hand_end AS on_hand,
               CAST(coalesce(oo.on_order, 0) AS BIGINT) AS on_order, i.near_expiry_units, round(a.avg28, 3) AS avg_daily_units_28d,
               round(i.on_hand_end / greatest(a.avg28, 0.1), 2) AS days_of_cover
        FROM silver.inventory i JOIN silver.products pr USING (sku) JOIN silver.stores st USING (store_id)
        LEFT JOIN oo USING (store_id, sku) LEFT JOIN a USING (store_id, sku)
        WHERE i.day = {as_of}""",
    "supplier_performance": """
        SELECT s.supplier_id, s.name AS supplier_name, CAST(count(*) AS BIGINT) AS orders, s.nominal_lead_days,
               round(avg(po.lead_time_days), 3) AS lead_time_mean, CAST(ceil(quantile_cont(po.lead_time_days, 0.9)) AS BIGINT) AS lead_time_p90,
               round(100 * avg(CASE WHEN po.on_time THEN 1 ELSE 0 END), 2) AS on_time_pct,
               round(100 * sum(po.qty_received) / sum(po.qty_ordered), 2) AS fill_rate_pct
        FROM silver.purchase_orders po JOIN silver.suppliers s USING (supplier_id)
        WHERE po.received_day IS NOT NULL GROUP BY s.supplier_id, s.name, s.nominal_lead_days""",
    "promo_plan": "SELECT p.sku, pr.category, p.start_day, p.end_day, p.price_ratio, p.display FROM silver.promotions p JOIN silver.products pr USING (sku)",
    "customer_segments": """
        WITH v AS (SELECT c.customer_key, c.home_store,
                          count(v.day) FILTER (WHERE v.day > {as_of} - 28) AS recent, count(v.day) AS visits, avg(v.basket_usd) AS basket
                   FROM silver.loyalty_customers c LEFT JOIN silver.loyalty_visits v USING (customer_key) GROUP BY ALL),
             s AS (SELECT *, CASE WHEN recent >= 4 THEN 'weekly regular' WHEN recent >= 1 THEN 'occasional' ELSE 'lapsed' END AS segment FROM v)
        SELECT home_store AS store_id, segment, CAST(count(*) AS BIGINT) AS members, CAST(sum(visits) AS BIGINT) AS visits, round(avg(basket), 2) AS avg_basket_usd
        FROM s GROUP BY ALL HAVING count(*) >= {k_min}""",
    "store_notes": "SELECT n.note_id, n.store_id, st.region, n.day, n.author_role, n.text_redacted, n.injection_flag FROM silver.store_notes n JOIN silver.stores st USING (store_id)",
}
K_MIN = 10  # k-anonymity threshold for customer_segments


def pseudonym(customer_id: str) -> str:
    """Keyed hash (HMAC-SHA256) of a customer id. In Azure the key lives in Key Vault, held by the privacy office."""
    key = os.environ.get("ADL_PSEUDONYM_KEY", "local-demo-pseudonymisation-key").encode()
    return hmac.new(key, customer_id.encode(), hashlib.sha256).hexdigest()[:16]


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
    return {"assertions": [{"assertion": r.check, "column": r.target, "success": r.passed} for r in q.results]}


def land_bronze(b: Build, tables: dict[str, pa.Table]) -> None:
    for name, t in tables.items():
        b.store.write("bronze", name, t)
        b.bronze_rows[name] = t.num_rows
        b.lineage.run(f"bronze.{name}", [f"source://{SOURCES[name]}/{name}"], [{"name": f"bronze.{name}", "schema": _schema(t), "rows": t.num_rows}])


def _conform(b: Build, table: str, data: pa.Table, inputs: list[str]) -> None:
    """Split rows by the contract's row-level checks, write valid rows and quarantine, evaluate, emit lineage."""
    c = b.contracts[f"retail.silver.{table}"]
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
    b.lineage.run(f"silver.{table}", inputs, outs)


def build_silver(b: Build) -> None:
    s = b.store
    for table, sql in SILVER_SQL.items():
        data = s.arrow(sql.format(as_of=AS_OF, days=W.PLAN_DAYS))
        _conform(b, table, data, [f"bronze.{x.split('.')[-1]}" for x in b.contracts[f"retail.silver.{table}"].inputs])
    cust = s.read("bronze", "loyalty_customers").to_pylist()
    keyed = [{"customer_key": pseudonym(r["customer_id"]), **r} for r in cust]
    _conform(
        b,
        "loyalty_customers",
        pa.Table.from_pylist(
            [
                {
                    "customer_key": r["customer_key"],
                    "postcode_district": r["postcode"].split()[0],
                    "home_store": r["home_store"],
                    "joined_day": r["joined_day"],
                }
                for r in keyed
            ]
        ),
        ["bronze.loyalty_customers"],
    )
    _conform(
        b,
        "loyalty_pii",
        pa.Table.from_pylist([{k: r[k] for k in ("customer_key", "full_name", "email", "phone", "postcode")} for r in keyed]),
        ["bronze.loyalty_customers"],
    )
    visits = s.read("bronze", "loyalty_visits").to_pylist()
    _conform(
        b,
        "loyalty_visits",
        pa.Table.from_pylist(
            [{"customer_key": pseudonym(v["customer_id"]), "store_id": v["store_id"], "day": v["day"], "basket_usd": v["basket_usd"]} for v in visits]
        ),
        ["bronze.loyalty_visits", "silver.loyalty_customers"],
    )
    names = sorted({r["full_name"] for r in cust}, key=len, reverse=True)
    notes = []
    for n in s.read("bronze", "store_notes").to_pylist():
        text, masked = guardrails.redact(n["text"])
        for name in names:  # known loyalty members' names, matched against the restricted PII table
            if name in text:
                text, masked = text.replace(name, "[NAME]"), True
        notes.append(
            {
                "note_id": n["note_id"],
                "store_id": n["store_id"],
                "day": n["day"],
                "author_role": n["author_role"],
                "text_redacted": text,
                "injection_flag": guardrails.screen(n["text"]).flagged,
                "pii_redacted": masked,
            }
        )
    _conform(b, "store_notes", pa.Table.from_pylist(notes), ["bronze.store_notes"])


def build_gold(b: Build, products: list[str] | None = None) -> None:
    for table, sql in GOLD_SQL.items():
        if products and table not in products:
            continue
        write_gold(b, table, b.store.arrow(sql.format(as_of=AS_OF, k_min=K_MIN)))


def write_gold(b: Build, table: str, data: pa.Table, extra_inputs: list[str] | None = None) -> ProductQuality:
    c = b.contracts[f"retail.gold.{table}"]
    cols = ", ".join(f'"{x}"' for x in c.columns)
    b.store.write("gold", table, data)
    data = b.store.arrow(f"SELECT {cols} FROM gold.{table}")  # enforce declared column order
    b.store.write("gold", table, data)
    q = evaluate(c, b.store, b.contracts, 0, AS_OF)
    b.quality[c.id] = q
    inputs = [f"{x.split('.')[1]}.{x.split('.')[2]}" for x in c.inputs] + (extra_inputs or [])
    b.lineage.run(f"gold.{table}", inputs, [{"name": f"gold.{table}", "schema": _schema(data), "rows": data.num_rows, "dq": _dq_facet(q)}])
    return q

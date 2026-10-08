"""Bronze -> silver -> gold: faults are caught, personal data is protected, every product meets its contract."""

import pytest

from adl.domains.retail import pipeline as P
from adl.domains.retail import runtime


def test_every_product_passes(valued):
    lk, _ = valued
    failed = {k: [r.check for r in q.results if not r.passed] for k, q in lk.build.quality.items() if not q.passed}
    assert not failed
    assert len(lk.build.quality) == 25


@pytest.mark.parametrize(
    "where,count",
    [("units < 0", 12), ("sku IS NULL", 6), ("sku = 'SKU-XX99'", 1)],
    ids=["negative units", "null sku", "unknown sku"],
)
def test_injected_faults_are_quarantined(lake, where, count):
    assert lake.store.sql(f"SELECT COUNT(*) AS n FROM silver.quarantine_pos_sales WHERE {where}")[0]["n"] == count


def test_resent_batch_is_deduplicated(lake):
    b = lake.build
    assert b.bronze_rows["pos_sales"] - b.quality["retail.silver.pos_sales"].rows - b.quarantined["retail.silver.pos_sales"] == 48
    dupes = lake.store.sql("SELECT COUNT(*) AS n FROM (SELECT store_id, sku, day FROM silver.pos_sales GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)")
    assert dupes[0]["n"] == 0


def test_silver_loyalty_has_no_direct_identifiers(lake):
    cols = lake.store.read("silver", "loyalty_customers").column_names
    assert not {"full_name", "email", "phone", "customer_id"} & set(cols)
    assert "customer_key" in cols


def test_pseudonym_is_keyed(monkeypatch):
    a = P.pseudonym("C0001")
    monkeypatch.setenv("ADL_PSEUDONYM_KEY", "another-key")
    assert P.pseudonym("C0001") != a and len(a) == 16


def test_store_notes_are_redacted_and_screened(lake):
    notes = lake.store.sql("SELECT * FROM silver.store_notes")
    text = " ".join(n["text_redacted"] for n in notes)
    assert "Ava Hartley" not in text and "555-0142" not in text and "[NAME]" in text
    assert sorted(n["note_id"] for n in notes if n["injection_flag"]) == ["NOTE-006", "NOTE-010", "NOTE-014"]


def test_customer_segments_are_k_anonymous(lake):
    assert lake.store.sql("SELECT MIN(members) AS m FROM gold.customer_segments")[0]["m"] >= P.K_MIN


def test_open_purchase_orders_have_no_receipt(lake):
    rows = lake.store.sql("SELECT COUNT(*) AS n FROM silver.purchase_orders WHERE received_day > ?", [P.AS_OF])
    assert rows[0]["n"] == 0


def test_gold_tables_are_delta_with_history(lake):
    h = lake.store.history("gold", "sales_daily")
    assert h and lake.store.uri("gold", "sales_daily").endswith("gold/sales_daily")


def test_insight_products_cover_every_store_product(lake):
    assert lake.store.sql("SELECT COUNT(*) AS n FROM gold.stockout_risk")[0]["n"] == 384
    assert lake.store.sql("SELECT COUNT(DISTINCT horizon) AS n FROM gold.demand_forecast")[0]["n"] == 14


def test_build_is_deterministic(lake, tmp_path):
    again = runtime.lake.__wrapped__(str(tmp_path))  # a second, uncached build
    q = "SELECT ROUND(SUM(revenue_usd), 4) AS r, SUM(units) AS u FROM gold.sales_daily"
    assert again.store.sql(q) == lake.store.sql(q)
    q2 = "SELECT ROUND(SUM(probability), 6) AS p FROM gold.stockout_risk"
    assert again.store.sql(q2) == lake.store.sql(q2)


def test_store_rejects_bad_names(lake):
    with pytest.raises(ValueError):
        lake.store.uri("gold", "sales; DROP")
    with pytest.raises(ValueError):
        lake.store.uri("platinum", "x")

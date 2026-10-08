"""Data quality, OpenLineage events and the governed metrics layer."""

import pyarrow as pa
import pytest

from adl import ROOT
from adl.core.contracts import load_all
from adl.core.lineage import Lineage, validate_event
from adl.core.quality import evaluate
from adl.core.semantic import SemanticLayer
from adl.storage.local import LocalDeltaStore


def test_quality_fails_a_table_that_breaks_its_contract(tmp_path):
    cs = load_all(ROOT / "domains/retail/contracts")
    st = LocalDeltaStore(tmp_path)
    st.write("silver", "stores", pa.table({"store_id": ["S01"], "name": ["Harbour"], "region": ["north"], "city": ["x"], "opened_day": [0]}))
    st.write("silver", "products", pa.table({"sku": ["SKU-PR01"]}))
    bad = pa.table(
        {
            "store_id": ["S01", "S01"],
            "region": ["north", "north"],
            "sku": ["SKU-PR01", "SKU-PR01"],
            "horizon_days": [3, 3],
            "available_units": [1.0, 1.0],
            "expected_demand": [2.0, 2.0],
            "probability": [1.4, 0.2],
            "risk_band": ["extreme", "low"],
        }
    )
    st.write("gold", "stockout_risk", bad)
    q = evaluate(cs["retail.gold.stockout_risk"], st, cs, 0, 139)
    failed = {r.check for r in q.results if not r.passed}
    assert not q.passed and {"unique", "range", "accepted_values"} <= failed


def test_lineage_events_are_valid_openlineage(valued):
    lk, _ = valued
    events = lk.build.lineage.events
    assert len(events) == 78 and all(validate_event(e) == [] for e in events)
    starts = [e["run"]["runId"] for e in events if e["eventType"] == "START"]
    completes = [e["run"]["runId"] for e in events if e["eventType"] == "COMPLETE"]
    assert starts == completes  # every job that started completed


def test_value_ledger_lineage_reaches_the_pos_source(valued):
    up = valued[0].build.lineage.upstream("gold.value_ledger")
    assert "source://pos/pos_sales" in up and "model.value_simulation" in up


def test_run_ids_are_deterministic():
    a, b = Lineage(), Lineage()
    ra = a.run("silver.x", ["bronze.x"], [{"name": "silver.x", "rows": 1}])
    rb = b.run("silver.x", ["bronze.x"], [{"name": "silver.x", "rows": 1}])
    assert ra == rb


def test_validate_event_reports_missing_fields():
    assert validate_event({"eventType": "COMPLETE"})


@pytest.fixture(scope="module")
def sem():
    return SemanticLayer.load(ROOT / "domains/retail/metrics.yaml")


def test_metrics_compile_with_bound_parameters(sem):
    sql, params = sem.compile(["revenue_usd"], ["region"], [("store_id", "in", ["S01", "S02"]), ("category", "eq", "dairy")])
    assert params == ["S01", "S02", "dairy"] and "S01" not in sql and "GROUP BY" in sql


@pytest.mark.parametrize(
    "metrics,by,filters",
    [
        (["nope"], None, None),
        ([], None, None),
        (["units"], ["cogs"], None),
        (["units"], None, [("cogs_usd", "eq", 1)]),
        (["units"], None, [("sku", "like", "x")]),
    ],
    ids=["unknown metric", "no metric", "unknown dimension", "filter on a non-dimension", "bad operator"],
)
def test_metrics_reject_bad_requests(sem, metrics, by, filters):
    with pytest.raises(ValueError):
        sem.compile(metrics, by, filters)


def test_metric_values_are_consistent(lake, sem):
    total = sem.query(lake.store, ["revenue_usd", "units"])[0]
    by_region = sem.query(lake.store, ["revenue_usd"], ["region"])
    assert abs(sum(r["revenue_usd"] for r in by_region) - total["revenue_usd"]) < 1
    raw = lake.store.sql("SELECT SUM(units) AS u FROM gold.sales_daily")[0]["u"]
    assert total["units"] == raw


def test_a_filter_value_is_data_not_sql(lake, sem):
    rows = sem.query(lake.store, ["units"], None, [("sku", "eq", "SKU-PR01' OR '1'='1")])
    assert rows[0]["units"] is None

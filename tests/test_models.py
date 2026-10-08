"""Forecast, stockout risk, elasticity and markdown: each is measured against a simple baseline."""

import ast

import numpy as np
import pytest

from adl import ROOT
from adl.domains.retail import markdown as MD
from adl.domains.retail import runtime
from adl.domains.retail import stockout as SO
from adl.domains.retail.world import CATEGORIES


@pytest.fixture(scope="module")
def fbt():
    return {r["model"]: r for r in runtime.forecast_backtest().rows}


def test_forecast_beats_both_naive_baselines(fbt):
    assert fbt["ridge model"]["wape_pct"] < fbt["28-day mean"]["wape_pct"] < fbt["same day last week"]["wape_pct"]


def test_forecast_bias_is_small(fbt):
    assert abs(fbt["ridge model"]["bias_pct"]) < 5


def test_forecast_wins_in_every_category():
    rows = runtime.forecast_backtest().by_category
    get = {(r["model"], r["category"]): r["wape_pct"] for r in rows}
    for c in CATEGORIES:
        assert get[("ridge model", c)] < get[("28-day mean", c)]


def test_forecasts_are_non_negative_and_bounded(lake):
    r = lake.store.sql("SELECT MIN(forecast_units) AS lo, MIN(p90_units - forecast_units) AS gap FROM gold.demand_forecast")[0]
    assert r["lo"] >= 0 and r["gap"] >= 0


def test_stockout_model_beats_the_cover_rule():
    model, rule = runtime.risk_backtest().rows
    assert model["f1_pct"] > rule["f1_pct"] and model["precision_pct"] > rule["precision_pct"]
    assert model["auc"] > 0.75


def test_risk_falls_as_stock_rises():
    fc = np.full((3, 3), 4.0)
    cv = np.full(3, 0.3)
    p, _ = SO.window_probability(fc, cv, np.array([0.0, 8.0, 30.0]), np.zeros((3, 3)))
    assert p[0] > p[1] > p[2]


def test_a_delivery_counts_from_the_next_day():
    fc = np.array([[5.0, 5.0, 5.0]])
    cv = np.array([0.1])
    p_with, _ = SO.window_probability(fc, cv, np.array([2.0]), np.array([[50.0, 0, 0]]))
    p_day1, _ = SO.probability(fc[:, :1], cv, np.array([2.0]))
    p_on_shelf, _ = SO.window_probability(fc, cv, np.array([52.0]), np.zeros((1, 3)))
    assert p_with[0] == pytest.approx(p_day1[0]) and p_with[0] > 0.8  # the delivery cannot save day one
    assert p_on_shelf[0] < 0.01


def test_overdue_orders_count_as_due_tomorrow():
    rows = [{"store_id": "S01", "sku": "A", "order_day": 100, "expected_day": 105, "received_day": None, "qty_ordered": 12}]
    due = SO.due_by_day(rows, [("S01", "A")], 110)
    assert due.tolist() == [[12.0, 0.0, 0.0]]


def test_risk_bands():
    assert SO.band(np.array([0.1, 0.3, 0.7])).tolist() == ["low", "medium", "high"]


def test_auc_handles_ties():
    assert SO.auc(np.array([True, False]), np.array([0.5, 0.5])) == 0.5
    assert SO.auc(np.array([True, False]), np.array([0.9, 0.1])) == 1.0


def test_elasticity_from_price_tests_has_the_right_sign(lake):
    assert all(v < 0 for v in lake.elasticity.values())


def test_elasticity_is_in_the_right_range(lake):
    for c, v in lake.elasticity.items():
        assert abs(v - CATEGORIES[c]["elasticity"]) < 0.7, c


def test_markdown_never_exceeds_the_cap():
    d = MD.choose(np.array([10.0, 0.0, 5.0]), np.array([2.0, 2.0, 0.5]), np.array([0.6, 0.6, 0.6]), np.array([-1.5, -1.5, -1.5]), 0.3)
    assert d.max() <= 0.3 and d[1] == 0


def test_markdown_candidates_respect_policy(lake):
    r = lake.store.sql("SELECT MAX(recommended_discount_pct) AS m, MIN(near_expiry_units) AS n FROM gold.markdown_candidates")[0]
    assert r["m"] <= 100 * lake.policy["markdown"]["max_discount"] and r["n"] > 0


def test_product_code_never_reads_ground_truth():
    # the simulators and evaluation harnesses may read the ground truth; product code never does
    allowed = {"domains/retail/synth.py", "cli.py", "domains/mortgage/world.py", "domains/insurance/world.py"}
    for p in (ROOT / "src/adl").rglob("*.py"):
        if str(p.relative_to(ROOT / "src/adl")) in allowed:
            continue
        tree = ast.parse(p.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "ground_truth" not in names | attrs, p

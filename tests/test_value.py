"""Value: simulation ledger with intervals, KPI targets, cost per outcome and the FOCUS export."""

import csv
import io
import json

import numpy as np
import pytest

from adl.domains.retail import runtime
from adl.domains.retail import simulate as SIM
from adl.domains.retail import value as V


@pytest.fixture(scope="module")
def led(valued):
    return valued[1]


def test_ledger_has_three_levers_by_four_metrics(led):
    assert len(led.rows) == 12 and len({r["ledger_id"] for r in led.rows}) == 12
    assert {r["lever"] for r in led.rows} == {"all levers", "replenishment", "markdown"}


def test_every_interval_contains_its_estimate(led):
    for r in led.rows:
        assert r["ci_low_usd"] <= r["delta_usd"] <= r["ci_high_usd"], r["ledger_id"]
        assert r["delta_usd"] == pytest.approx(r["agent_usd"] - r["baseline_usd"], abs=0.02)


def test_all_levers_add_value_with_an_interval_above_zero(led):
    net = led.row("all levers", "net_value_usd")
    assert net["ci_low_usd"] > 0


def test_levers_are_reported_separately(led):
    rep = led.row("replenishment", "net_value_usd")["delta_usd"]
    md = led.row("markdown", "net_value_usd")["delta_usd"]
    both = led.row("all levers", "net_value_usd")["delta_usd"]
    assert rep > md > 0 and both > rep


def test_markdown_alone_increases_waste_honestly(led):
    assert led.row("markdown", "waste_cost_usd")["delta_usd"] > 0


def test_value_ledger_is_a_gold_product(valued):
    lk, _ = valued
    assert lk.store.sql("SELECT COUNT(*) AS n FROM gold.value_ledger")[0]["n"] == 12


def test_kpi_results_are_judged_against_targets(led):
    for k in V.kpi_results(led.comparison):
        hit = k["change_pct"] <= k["target_change_pct"] if k["target_change_pct"] < 0 else k["change_pct"] >= k["target_change_pct"]
        assert k["met"] == hit


def test_bootstrap_is_deterministic():
    d = np.arange(30.0)
    assert SIM.bootstrap_ci(d) == SIM.bootstrap_ci(d)
    lo, hi = SIM.bootstrap_ci(d)
    assert lo < d.mean() < hi


def test_value_case_baselines_come_from_gold(lake):
    rows = V.value_case(lake.store, lake.semantic)
    assert [r["metric"] for r in rows] == [k["metric"] for k in V.load_case()["kpis"]]
    for r in rows:
        assert r["target"] == pytest.approx(r["baseline"] * (1 + r["target_change_pct"] / 100))


def test_cost_estimate_adds_up():
    c = V.ai_cost(600, 400, 8)
    assert c.briefs == 224
    assert c.total_usd == pytest.approx(sum(x["cost_usd"] for x in c.lines))
    assert c.per_1000_value(0) == float("inf")


@pytest.fixture(scope="module")
def focus():
    cost = V.ai_cost(600, 400, 8)
    return cost, V.focus_rows(cost, "VL-RET-001")


def test_focus_rows_cover_every_service_every_day(focus):
    cost, rows = focus
    assert len(rows) == 28 * len(cost.lines)
    assert sum(r["EffectiveCost"] for r in rows) == pytest.approx(cost.total_usd, abs=0.01)


def test_focus_csv_has_the_finops_columns(focus):
    _, rows = focus
    text = V.focus_csv(rows)
    header = next(csv.reader(io.StringIO(text)))
    assert header == V.FOCUS_COLUMNS and len(header) == 29


def test_focus_tags_link_cost_to_the_ledger(focus):
    tags = json.loads(focus[1][0]["Tags"])
    assert tags["value-ledger"] == "VL-RET-001" and tags["env"] == "simulation"


def test_measured_prompt_size_feeds_the_cost(valued):
    _gw, _plans, cases = runtime.agent_run()
    assert all(c.prompt_tokens > 100 and c.output_tokens > 10 for c in cases)

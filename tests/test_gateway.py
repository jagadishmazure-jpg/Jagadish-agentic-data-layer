"""The data gateway: every rule, every attack, the audit chain."""

import pytest

from adl.core.access import GLOBAL_ROW_CAP, AccessDenied
from adl.domains.retail import attacks


@pytest.mark.parametrize("attempt", attacks.ATTEMPTS, ids=lambda a: a.name)
def test_attack_is_stopped_with_the_right_code(gw, attempt):
    res = {r["attempt"]: r for r in attacks.run(gw)}[attempt.name]
    assert res["got"] == attempt.expect


def test_every_attack_is_audited(gw):
    attacks.run(gw)
    denials = sum(len(gw.audit.events(e)) for e in ("data.denied", "metric.denied", "knowledge.denied"))
    assert denials == len(attacks.ATTEMPTS) and gw.audit.verify()[0]


def test_no_pii_in_any_agent_exposed_product(gw):
    assert sum(attacks.pii_leaks(gw).values()) == 0


def test_tampering_breaks_the_chain(gw):
    gw.query("agent:replenishment", "supplier_performance", "replenishment")
    gw.query("agent:replenishment", "supplier_performance", "replenishment")
    detected, msg = attacks.tamper_detected(gw)
    assert detected and "mismatch" in msg


def test_row_level_security_limits_a_regional_copilot(gw):
    rows = gw.query("agent:store-copilot-north", "inventory_position", "store_operations", limit=200)
    assert rows and {r["region"] for r in rows} == {"north"}


def test_column_level_security_hides_denied_columns(gw):
    d = gw.describe("agent:store-copilot-north", "sales_daily")
    assert "cogs_usd" not in {c["name"] for c in d["columns"]}
    rows = gw.query("agent:store-copilot-north", "inventory_position", "store_operations", limit=1)
    assert "unit_cost" not in rows[0]


def test_unscoped_agent_sees_every_store(gw):
    rows = gw.query("agent:replenishment", "inventory_position", "replenishment", limit=500)
    assert len({r["store_id"] for r in rows}) == 8


def test_free_text_is_quoted_and_flagged(gw):
    rows = gw.query("agent:replenishment", "store_notes", "replenishment", filters=[["store_id", "eq", "S05"]])
    assert all(r["text_redacted"].startswith("<untrusted_data>") for r in rows)
    assert any(r["injection_flag"] for r in rows)


def test_filters_and_limits(gw):
    rows = gw.query(
        "agent:replenishment",
        "sales_daily",
        "replenishment",
        columns=["store_id", "sku", "units"],
        filters=[["units", "ge", 40], ["store_id", "in", ["S01", "S02"]]],
        limit=5,
    )
    assert len(rows) == 5 and all(r["units"] >= 40 and r["store_id"] in {"S01", "S02"} for r in rows)
    with pytest.raises(AccessDenied):
        gw.query("agent:replenishment", "sales_daily", "replenishment", limit=GLOBAL_ROW_CAP + 1)


@pytest.mark.parametrize(
    "filters,code",
    [
        ([["units", "ge", "40"]], "bad_filter_value"),
        ([["units"]], "bad_filter"),
        ([["store_id", "in", []]], "bad_filter_value"),
        ([["store_id", "in", [f"S{i}" for i in range(60)]]], "bad_filter_value"),
        ([["nope", "eq", 1]], "unknown_column"),
    ],
    ids=["string for an integer", "malformed filter", "empty IN", "IN too long", "unknown column"],
)
def test_bad_filters_are_refused(gw, filters, code):
    with pytest.raises(AccessDenied) as e:
        gw.query("agent:replenishment", "sales_daily", "replenishment", filters=filters)
    assert e.value.code == code


def test_metrics_are_row_scoped(gw):
    north = gw.metric("agent:store-copilot-north", ["revenue_usd"], "store_operations", ["region"])
    assert [r["region"] for r in north] == ["north"]


def test_list_products_shows_only_grants(gw):
    names = {p["name"] for p in gw.list_products("agent:finance")}
    assert names == {"value_ledger", "sales_daily"}


def test_reads_are_audited_with_row_scope(gw):
    gw.query("agent:store-copilot-south", "stockout_risk", "store_operations", limit=10)
    rec = gw.audit.events("data.read")[-1]
    assert rec["actor"] == "agent:store-copilot-south" and rec["data"]["row_scope"]["region"] == ["south"]

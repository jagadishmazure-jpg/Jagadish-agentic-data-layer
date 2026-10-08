"""Shared platform pieces added with the mortgage domain: the approval workflow core and FOCUS cost rows."""

import csv
import io

from adl.core import agentflow as F
from adl.core import finops


def _acts():
    return [
        F.Action("M0001", "outreach_call", "LO01", "APP-00001", 1, 120.0, "risk", False, "loan_officer"),
        F.Action("M0002", "lock_extension", "LO01", "APP-00002", 7, 650.0, "expiring", True, "pipeline_manager"),
    ]


def test_digest_binds_every_planned_field():
    a = _acts()
    d = F.digest(a)
    assert d == F.digest(list(a)) and len(d) == 16
    b = [a[0], F.Action(**{**a[1].__dict__, "quantity": 14})]
    assert F.digest(b) != d
    assert F.digest(a[:1]) != d


def test_case_pending_is_the_approval_set():
    c = F.Case("LO01", actions=_acts())
    assert [x.id for x in c.pending] == ["M0002"]


def test_validator_catches_added_changed_dropped_and_wrong_group():
    c = F.Case("LO01", actions=_acts())
    ok = F.fallback_brief(c)
    assert F.validate_brief(ok, c) == []
    bad = ok.model_copy(deep=True)
    bad.actions[1].quantity = 21
    bad.actions.append(F.BriefAction(id="X1", kind="lock_extension", subject="APP-99999", quantity=7))
    bad.group = "LO02"
    issues = F.validate_brief(bad, c)
    assert any("changes M0002" in i for i in issues) and any("did not plan" in i for i in issues) and "wrong group" in issues
    short = ok.model_copy(deep=True)
    short.actions = short.actions[:1]
    assert any("drops 1" in i for i in F.validate_brief(short, c))


def test_ai_cost_adds_up():
    est = finops.ai_cost(500, 300, 12, 28)
    assert est.briefs == 336 and abs(sum(x["cost_usd"] for x in est.lines) - est.total_usd) < 1e-9
    assert est.per_1000_value(0) == float("inf") and est.per_1000_value(1000) == est.total_usd


def test_focus_rows_and_csv():
    est = finops.ai_cost(500, 300, 12, 28)
    rows = finops.focus_rows(est, 28, {"domain": "demo"}, "ba-demo", "sub-demo")
    assert len(rows) == 28 * len(est.lines) and set(rows[0]) == set(finops.FOCUS_COLUMNS)
    assert abs(sum(r["EffectiveCost"] for r in rows) - est.total_usd) < 0.01
    parsed = list(csv.DictReader(io.StringIO(finops.focus_csv(rows))))
    assert len(parsed) == len(rows) and list(parsed[0]) == finops.FOCUS_COLUMNS

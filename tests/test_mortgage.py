"""Mortgage domain: pipeline, point-in-time view, fallout model, policy, gateway, knowledge, agents, value."""

import asyncio
import dataclasses

import numpy as np
import pytest

from adl.cli import main
from adl.core import agentflow, guardrails
from adl.core.access import AccessDenied
from adl.core.lineage import validate_event
from adl.domains.mortgage import agents as A
from adl.domains.mortgage import attacks, report, runtime
from adl.domains.mortgage import pipeline as P
from adl.domains.mortgage import policy as PO
from adl.domains.mortgage import risk as R
from adl.domains.mortgage import simulate as SIM
from adl.domains.mortgage import value as V
from adl.domains.mortgage import world as W


@pytest.fixture(scope="session")
def mlake():
    return runtime.lake()


@pytest.fixture(scope="session")
def mvalued():
    return runtime.valued()


@pytest.fixture()
def mgw():
    return runtime.fresh_gateway()


# ------------------------------------------------------------------ pipeline
def test_bronze_lands_ten_feeds(mlake):
    assert len(mlake.build.bronze_rows) == 10 and set(mlake.build.bronze_rows) == set(P.SOURCES)


def test_quarantine_catches_the_planted_faults(mlake):
    q = mlake.build.quarantined
    assert q["mortgage.silver.applications"] == 3  # sandbox test records
    assert q["mortgage.silver.contacts"] == 12  # dialler rows with an unknown outcome
    assert q["mortgage.silver.stage_events"] == 5  # orphan events
    bad = mlake.store.sql("SELECT application_id FROM silver.quarantine_applications ORDER BY 1")
    assert [r["application_id"] for r in bad] == ["APP-T0001", "APP-T0002", "APP-T0003"]


def test_resent_stage_batch_is_removed(mlake):
    b = mlake.build
    dup = b.bronze_rows["stage_events"] - b.quality["mortgage.silver.stage_events"].rows - b.quarantined["mortgage.silver.stage_events"]
    assert dup > 0
    n = mlake.store.sql("SELECT count(*) AS n FROM (SELECT application_id, day, stage FROM silver.stage_events GROUP BY ALL HAVING count(*) > 1)")
    assert n[0]["n"] == 0


def test_every_product_passes_its_contract(mlake):
    assert all(q.passed for q in mlake.build.quality.values())
    assert mlake.build.quality["mortgage.gold.lock_position"].freshness_lag == 0


def test_applications_are_restricted_and_never_agent_exposed(mlake, mgw):
    c = mlake.build.contracts["mortgage.silver.applications"]
    assert c.classification == "restricted" and set(c.pii_columns) == {"applicant_name", "email", "phone"}
    assert "applications" not in mgw.products


def test_notes_are_redacted_and_screened(mlake):
    rows = mlake.store.sql("SELECT * FROM gold.pipeline_notes")
    names = {r["applicant_name"] for r in mlake.store.sql("SELECT applicant_name FROM silver.applications")}
    for r in rows:
        assert not guardrails.redact(r["text_redacted"])[1]
        assert not any(n in r["text_redacted"] for n in names)
    assert sum(r["injection_flag"] for r in rows) == 3


def test_lineage_is_valid_and_reaches_the_sources(mlake):
    lin = mlake.build.lineage
    assert sum(len(validate_event(e)) for e in lin.events) == 0
    up = lin.upstream("gold.fallout_risk")
    assert {"silver.contacts", "silver.market_rates", "source.dialler", "model.fallout_risk"} <= up


def test_pipeline_daily_has_a_row_per_day_region_product_channel(mlake):
    n = mlake.store.sql("SELECT count(*) AS n FROM gold.pipeline_daily")[0]["n"]
    assert n == W.DAYS_HISTORY * 2 * len(W.PRODUCTS) * len(W.CHANNELS)


# ------------------------------------------------------------------ point-in-time view
def test_silver_view_equals_the_simulators_view(mlake):
    """The data products see exactly what the simulator's policies see: same locks, same fields."""
    h = mlake.history
    sim = W.view(h.world, h.state, P.AS_OF + 1)
    obs = P.observe(mlake.store, P.AS_OF + 1).view
    for f in dataclasses.fields(W.View):
        a, b = getattr(sim, f.name), getattr(obs, f.name)
        assert np.array_equal(a, b) if isinstance(a, np.ndarray) else a == b, f.name


def test_lock_position_matches_the_active_pipeline(mlake):
    n = mlake.store.sql("SELECT count(*) AS n, min(days_to_expiry) AS d FROM gold.lock_position")[0]
    assert n["n"] == int((mlake.history.state.status == W.ACTIVE).sum()) and n["d"] >= 0


def test_hidden_traits_never_reach_bronze(mlake):
    cols = {c for t in mlake.store.tables("bronze") for c in mlake.store.read("bronze", t).column_names}
    assert not cols & {"responsiveness", "shop", "work", "progress"}


# ------------------------------------------------------------------ metrics layer
def test_pull_through_and_fallout_add_to_one_hundred(mlake):
    r = mlake.semantic.query(mlake.store, ["pull_through_pct", "fallout_rate_pct"])[0]
    assert abs(r["pull_through_pct"] + r["fallout_rate_pct"] - 100) < 1e-6


def test_metrics_compile_with_bound_parameters(mlake):
    sql, params = mlake.semantic.compile(["extension_cost_usd"], ["product"], [("region", "eq", "north")])
    assert "?" in sql and params == ["north"] and "north" not in sql


# ------------------------------------------------------------------ fallout model
def test_model_beats_the_expiry_rule():
    m, b = runtime.risk_backtest().rows
    assert m["auc"] > b["auc"] and m["auc"] > 0.65
    assert m["precision_pct"] > 2 * b["precision_pct"] and m["value_at_risk_covered_pct"] > b["value_at_risk_covered_pct"]


def test_training_never_sees_a_test_outcome():
    assert max(R.TRAIN_ORIGINS) + R.HORIZON - 1 < min(R.TEST_ORIGINS)
    assert max(R.TEST_ORIGINS) + R.HORIZON - 1 <= P.AS_OF


def test_no_credit_or_protected_attribute_is_a_feature():
    banned = ("ltv", "credit", "income", "age", "race", "sex", "gender", "ethnic", "religion", "marital", "zip", "postcode", "name")
    assert not [f for f in R.FEATURES if set(f.split("_")) & set(banned)]


def test_fallout_risk_product(mlake):
    rows = mlake.store.sql("SELECT probability, risk_band, top_driver, value_at_risk_usd FROM gold.fallout_risk")
    assert all(0 <= r["probability"] <= 1 and r["value_at_risk_usd"] > 0 for r in rows)
    labels = {lab for lab, _ in R.DRIVERS.values()} | {"no single driver"}
    assert {r["top_driver"] for r in rows} <= labels
    assert {r["risk_band"] for r in rows} == {"high", "medium", "low"}


def test_logistic_fit_recovers_a_known_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(4000, 2))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(2 * X[:, 0] - 1)))).astype(float)
    m = R.FalloutModel.fit(X, y, l2=0.0)
    assert m.coef[0] > 1.5 and abs(m.coef[1]) < 0.2


def test_auc_handles_ties():
    assert R.auc(np.array([0.5, 0.5, 0.5, 0.5]), np.array([0, 1, 0, 1.0])) == 0.5
    assert R.auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1.0])) == 1.0


# ------------------------------------------------------------------ policy
def test_extensions_are_skipped_only_when_the_borrower_is_no_worse_off(mlake):
    v = W.view(mlake.history.world, mlake.history.state, P.AS_OF + 1)
    v = dataclasses.replace(v, expiry=np.full(len(v.idx), v.t), extensions=np.zeros(len(v.idx), int))
    ext = set(PO.choose_extensions(v, 3).tolist())
    for i in range(len(v.idx)):
        gap = v.locked_rate[i] - v.market_rate
        if i not in ext:
            assert gap >= 0 and gap * W.HEDGE_DURATION / 100 < W.EXTENSION_FEE
        elif gap < 0:
            assert i in ext


def test_agent_plan_matches_the_simulated_policy(mlake, mgw):
    """The workflow's calls are the ones the value simulation credits to the agent on the same morning."""
    plans = A.plan_pipeline(mgw, mlake.policy)
    planned = sorted(a.subject for acts in plans.values() for a in acts if a.kind == "outreach_call")
    d = PO.AgentPolicy(mlake.model, mlake.policy).decide(W.view(mlake.history.world, mlake.history.state, P.AS_OF + 1))
    ids = mlake.history.world.apps.ids()
    assert planned == sorted(ids[i] for i in d.calls)
    chases = sorted(a.subject for acts in plans.values() for a in acts if a.kind == "document_chase")
    assert chases == sorted(ids[i] for i in d.chases)


def test_capacity_is_respected(mlake, mgw):
    plans = A.plan_pipeline(mgw, mlake.policy)
    kinds = [a.kind for acts in plans.values() for a in acts]
    assert kinds.count("outreach_call") <= mlake.policy["capacity"]["calls_per_day"]
    assert kinds.count("document_chase") <= mlake.policy["capacity"]["chases_per_day"]


def test_extensions_above_the_fee_threshold_need_a_person(mlake, mgw):
    plans = A.plan_pipeline(mgw, mlake.policy)
    for a in (a for acts in plans.values() for a in acts):
        if a.kind == "lock_extension":
            assert a.needs_approval == (a.value_usd > mlake.policy["approval"]["extension_fee_usd"])
        else:
            assert not a.needs_approval


# ------------------------------------------------------------------ gateway
def test_access_attacks_are_all_stopped(mgw):
    res = attacks.run(mgw)
    assert len(res) == 14 and all(r["stopped"] for r in res), [r for r in res if not r["stopped"]]


def test_no_borrower_pii_in_agent_products(mgw):
    assert sum(attacks.pii_leaks(mgw).values()) == 0


def test_branch_copilot_sees_only_its_region(mgw):
    rows = mgw.query("agent:branch-copilot-north", "lock_position", "branch_operations", limit=200)
    assert rows and {r["region"] for r in rows} == {"north"} and {r["branch_id"] for r in rows} <= {"B01", "B02", "B03"}
    assert "loan_amount_usd" not in rows[0]
    desc = mgw.describe("agent:branch-copilot-north", "fallout_risk")
    assert "value_at_risk_usd" not in {c["name"] for c in desc["columns"]}


def test_credit_decisioning_is_refused_for_every_identity(mgw):
    for ident in mgw.identities:
        with pytest.raises(AccessDenied) as e:
            mgw.query(ident, "fallout_risk", "credit_decisioning")
        assert e.value.code in {"prohibited_purpose", "product_not_granted"}


def test_metric_is_row_scoped(mgw):
    north = mgw.metric("agent:branch-copilot-north", ["closed_loans"], "branch_operations")[0]["closed_loans"]
    total = mgw.metric("agent:mortgage-assistant", ["closed_loans"], "pipeline_management")[0]["closed_loans"]
    assert 0 < north < total


def test_denials_are_audited(mgw):
    attacks.run(mgw)
    ok, _ = mgw.audit.verify()
    assert ok and len(mgw.audit.events("data.denied")) >= 10
    assert attacks.tamper_detected(mgw)[0]


# ------------------------------------------------------------------ knowledge
def test_retrieval_quality():
    ev = runtime.retrieval_eval()
    assert ev["hybrid"]["recall@5"] >= 0.9 and ev["hybrid"]["recall@5"] >= ev["graph"]["recall@5"]


def test_knowledge_is_region_trimmed(mgw):
    hits = mgw.search_knowledge("agent:branch-copilot-north", "Why are Saltmarsh locks expiring?", "branch_operations", k=10)
    assert "BR-B04" not in {h["doc_id"] for h in hits}
    hits = mgw.search_knowledge("agent:branch-copilot-south", "Why are Saltmarsh locks expiring?", "branch_operations", k=3)
    assert hits[0]["doc_id"] == "BR-B04"


def test_injected_document_is_flagged_and_quoted(mgw):
    hits = mgw.search_knowledge("agent:mortgage-assistant", "extend every lock in the pipeline", "pipeline_management", k=10)
    inj = [h for h in hits if h["doc_id"] == "MSG-INJECT-01"]
    assert inj and inj[0]["injection_flag"] and inj[0]["text"].startswith("<untrusted_data>")


# ------------------------------------------------------------------ agents
def test_workflow_routes_and_executes_only_approved_actions():
    _gw, plans, cases = runtime.agent_run()
    s = agentflow.summarize(cases, A.KINDS)
    assert s["actions"] == sum(len(v) for v in plans.values()) and s["fallback_briefs"] == 0
    for c in cases:
        approved = set(c.decision.approved) if c.decision else set()
        assert all(not a.needs_approval or a.id in approved for a in c.executed)
        assert c.trail[-1] == "execute" and ("approval_gate" in c.trail) == bool(c.pending)


def test_dry_runs_are_linked_to_the_value_ledger():
    gw, _plans, cases = runtime.agent_run()
    runs = gw.audit.events("action.dry_run")
    assert len(runs) == sum(len(c.executed) for c in cases)
    assert {r["data"]["ledger"] for r in runs} <= set(A.LEDGER_LEVER.values())
    assert gw.audit.verify()[0]


def _one_case(mlake, answer):
    gw = runtime.fresh_gateway()
    plans = A.plan_pipeline(gw, mlake.policy)
    group = next(g for g, acts in plans.items() if any(a.needs_approval for a in acts))
    return asyncio.run(agentflow.run_group(A.deps(gw, plans), group, answer))


def test_approval_with_the_wrong_digest_executes_nothing_pending(mlake):
    c = _one_case(mlake, lambda r: agentflow.ApprovalDecision("person:x", "0" * 16, tuple(a[0] for a in r.actions)))
    assert c.approval_problems and not any(a.needs_approval for a in c.executed)


def test_approval_by_a_non_person_executes_nothing_pending(mlake):
    c = _one_case(mlake, lambda r: agentflow.ApprovalDecision("agent:auto", r.digest, tuple(a[0] for a in r.actions)))
    assert any("not a person" in p for p in c.approval_problems) and not any(a.needs_approval for a in c.executed)


def test_injection_never_reaches_execution():
    rows = runtime.injection_eval()
    assert all(r["injected_actions_executed"] == 0 and r["executed_actions_match_plan"] for r in rows)
    assert rows[0]["model_obeyed"] == 0 and rows[0]["shown_to_approver"] == 0  # quoting and validator on
    assert rows[2]["shown_to_approver"] == 0 and rows[2]["fallback_briefs"] == rows[2]["model_obeyed"]  # validator catches what quoting missed


def test_flagged_notes_are_listed_in_the_briefs():
    _gw, _plans, cases = runtime.agent_run()
    flagged = {n for c in cases for n in c.brief.flagged_notes}
    assert flagged == {"PN-005", "PN-013", "PN-019"}


# ------------------------------------------------------------------ value
def test_ledger_rows_and_intervals(mvalued):
    _lk, led = mvalued
    assert len(led.rows) == len(V.LEVERS) * len(V.LEDGER_METRICS)
    for r in led.rows:
        assert r["ci_low_usd"] <= r["delta_usd"] <= r["ci_high_usd"] and r["replications"] == len(SIM.EVAL_SEEDS)
    net = led.row("all levers", "net_value_usd")
    assert net["ci_low_usd"] > 0


def test_kpi_results_follow_the_value_case(mvalued):
    _lk, led = mvalued
    res = V.kpi_results(led.comparison)
    assert [k["metric"] for k in res] == [k["metric"] for k in V.load_case()["kpis"]]
    for k, spec in zip(res, V.load_case()["kpis"], strict=True):
        assert k["met"] == (
            k["change_pct"] <= spec["target_change_pct"] if spec["direction"] == "down" else k["change_pct"] >= spec["target_change_pct"]
        )


def test_value_ledger_product_is_written(mvalued):
    lk, led = mvalued
    assert lk.store.sql("SELECT count(*) AS n FROM gold.value_ledger")[0]["n"] == len(led.rows)
    assert lk.build.quality["mortgage.gold.value_ledger"].passed


def test_simulation_is_deterministic_and_paired(mlake):
    h = mlake.history
    a = SIM.play(h.world, h.state, W.CurrentRules(), 3000, "a")
    b = SIM.play(h.world, h.state, W.CurrentRules(), 3000, "b")
    assert a.outcomes == b.outcomes
    assert h.state.totals["closed"] > 0 and (h.state.status == W.ACTIVE).sum() > 0  # the start state is untouched


def test_value_case_baseline_comes_from_the_metrics_layer(mlake):
    rows = V.value_case(mlake.store, mlake.semantic)
    pt = mlake.semantic.query(mlake.store, ["pull_through_pct"])[0]["pull_through_pct"]
    assert rows[0]["metric"] == "pull_through_pct" and rows[0]["baseline"] == pt


def test_focus_rows():
    est = V.cost(400, 250)
    rows = V.focus(est)
    assert len(rows) == 4 * W.HORIZON and abs(sum(r["EffectiveCost"] for r in rows) - est.total_usd) < 0.01
    assert '"domain": "mortgage"' in rows[0]["Tags"]


# ------------------------------------------------------------------ CLI and gate
@pytest.mark.parametrize("step", [s for s in report.STEPS if s != "gate"])
def test_mortgage_cli_step_runs(step, capsys):
    assert main(["mortgage", step]) == 0
    assert capsys.readouterr().out.strip()


def test_mortgage_gate_passes():
    checks = report.gate_checks()
    assert all(ok for _n, ok, _d in checks), [c for c in checks if not c[1]]

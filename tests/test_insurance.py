"""Insurance domain: pipeline, point-in-time view, claim models, policy, gateway, knowledge, agents,
value and the unfair-discrimination check."""

import asyncio
import dataclasses

import numpy as np
import pytest

from adl.cli import main
from adl.core import agentflow, guardrails
from adl.core.access import AccessDenied
from adl.core.lineage import validate_event
from adl.core.logit import Logistic, auc
from adl.domains.insurance import agents as A
from adl.domains.insurance import attacks, report, runtime
from adl.domains.insurance import fairness as FA
from adl.domains.insurance import models as M
from adl.domains.insurance import pipeline as P
from adl.domains.insurance import policy as PO
from adl.domains.insurance import simulate as SIM
from adl.domains.insurance import value as V
from adl.domains.insurance import world as W


@pytest.fixture(scope="session")
def ilake():
    return runtime.lake()


@pytest.fixture(scope="session")
def ivalued():
    return runtime.valued()


@pytest.fixture()
def igw():
    return runtime.fresh_gateway()


# ------------------------------------------------------------------ pipeline
def test_bronze_lands_twelve_feeds(ilake):
    assert len(ilake.build.bronze_rows) == 12 and set(ilake.build.bronze_rows) == set(P.SOURCES)


def test_quarantine_catches_the_planted_faults(ilake):
    q = ilake.build.quarantined
    assert q["insurance.silver.claims"] == 7  # sandbox test claims and impossible records
    assert q["insurance.silver.claim_events"] == 6
    assert q["insurance.silver.payment_requests"] == 3
    assert sum(q.values()) == 16


def test_intake_retries_are_removed(ilake):
    b = ilake.build
    dup = b.bronze_rows["fnol"] - b.quality["insurance.silver.claims"].rows - b.quarantined["insurance.silver.claims"]
    assert dup == 30
    n = ilake.store.sql("SELECT count(*) AS n FROM (SELECT claim_id FROM silver.claims GROUP BY 1 HAVING count(*) > 1)")
    assert n[0]["n"] == 0


def test_every_product_passes_its_contract(ilake):
    assert all(q.passed for q in ilake.build.quality.values())


def test_claims_and_proxy_groups_are_restricted_and_never_agent_exposed(ilake, igw):
    c = ilake.build.contracts["insurance.silver.claims"]
    assert c.classification == "restricted" and set(c.pii_columns) == {"claimant_name", "email", "phone"}
    assert ilake.build.contracts["insurance.silver.postcode_groups"].classification == "restricted"
    assert not {"claims", "postcode_groups"} & set(igw.products)


def test_notes_are_redacted_and_screened(ilake):
    rows = ilake.store.sql("SELECT * FROM gold.claim_notes")
    names = {r["claimant_name"] for r in ilake.store.sql("SELECT claimant_name FROM silver.claims")}
    for r in rows:
        assert not guardrails.redact(r["text_redacted"])[1]
        assert not any(n in r["text_redacted"] for n in names)
    assert {r["note_id"] for r in rows if r["injection_flag"]} == {"CN-005", "CN-021"}  # the pattern screen misses CN-013


def test_lineage_is_valid_and_reaches_the_sources(ilake):
    lin = ilake.build.lineage
    assert sum(len(validate_event(e)) for e in lin.events) == 0
    up = lin.upstream("gold.leakage_signals")
    assert {"silver.payment_requests", "model.claim_leakage", "model.claim_subrogation", "source.claims-system"} <= up


def test_claims_daily_has_a_row_per_day_region_line_queue(ilake):
    n = ilake.store.sql("SELECT count(*) AS n FROM gold.claims_daily")[0]["n"]
    assert n == W.DAYS_HISTORY * 2 * len(W.LINES) * len(W.QUEUES)


def test_no_agent_product_carries_the_proxy_group_or_postcode(ilake, igw):
    for p in igw.products:
        if p in ilake.store.tables("gold") and p != "fairness_monitor":
            cols = set(ilake.store.read("gold", p).column_names)
            assert not cols & {"proxy_group", "postcode_district", "group_name"}, p


# ------------------------------------------------------------------ point-in-time view
def test_silver_view_equals_the_simulators_view(ilake):
    """The data products see exactly what the simulator's policies see: same claims, same fields."""
    h = ilake.history
    sim = W.view(h.world, h.state, P.AS_OF + 1)
    obs = P.observe(ilake.store, P.AS_OF + 1).view
    for f in dataclasses.fields(W.View):
        a, b = getattr(sim, f.name), getattr(obs, f.name)
        assert np.array_equal(a, b) if isinstance(a, np.ndarray) else a == b, f.name


def test_hidden_traits_never_reach_bronze(ilake):
    cols = {c for t in ilake.store.tables("bronze") for c in ilake.store.read("bronze", t).column_names}
    assert not cols & {"complex", "true_cost", "leak_logit", "overpay_share", "tp_fault"}


# ------------------------------------------------------------------ metrics layer
def test_metrics_compile_with_bound_parameters(ilake):
    sql, params = ilake.semantic.compile(["leakage_usd"], ["queue"], [("region", "eq", "east")])
    assert "?" in sql and params == ["east"] and "east" not in sql


def test_leakage_metric_scales_audits_to_paid_claims(ilake):
    r = ilake.semantic.query(ilake.store, ["leakage_usd", "paid_claims"])[0]
    a = ilake.store.sql("SELECT sum(audit_overpayment_usd + audit_missed_recovery_usd) AS x, sum(audited_claims) AS n FROM gold.claims_daily")[0]
    assert abs(r["leakage_usd"] - a["x"] * r["paid_claims"] / a["n"]) < 1e-6 * r["leakage_usd"]


# ------------------------------------------------------------------ models
def test_leakage_and_subrogation_models_beat_their_rules():
    bt = runtime.backtest()
    (lm, lb), (sm, sb) = bt.leakage, bt.subrogation
    assert lm["auc"] > lb["auc"] and lm["precision_pct"] > lb["precision_pct"]
    assert sm["auc"] > sb["auc"] and sm["recall_pct"] > sb["recall_pct"]


def test_complexity_model_result_is_reported_honestly():
    m, r = runtime.backtest().complexity
    assert m["auc"] > r["auc"] and m["brier"] < r["brier"]
    # at the rule's queue sizes the model is barely better than the rule; the docs say so
    assert abs(m["complex_unit_precision_pct"] - r["complex_unit_precision_pct"]) < 5


def test_training_never_sees_a_test_label():
    assert max(M.TRAIN_REPORTED) < min(M.TEST_REPORTED) and max(M.TRAIN_AUDITS) < min(M.TEST_AUDITS)


def test_no_identity_postcode_or_group_is_a_feature():
    banned = ("name", "email", "phone_number", "postcode", "district", "group", "age", "sex", "gender", "race", "ethnic", "religion")
    feats = M.COMPLEXITY_FEATURES + M.LEAKAGE_FEATURES + M.SUBROGATION_FEATURES
    assert not [f for f in feats if any(b in f for b in banned)]


def test_triage_product(ilake):
    rows = ilake.store.sql("SELECT * FROM gold.claims_triage")
    assert rows and all(0 <= r["complexity_probability"] <= 1 for r in rows)
    labels = {lab for lab, _ in M.COMPLEXITY_DRIVERS.values()} | {"no single driver"}
    assert {r["top_driver"] for r in rows} <= labels


def test_logistic_fit_recovers_a_known_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(4000, 2))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(2 * X[:, 0] - 1)))).astype(float)
    m = Logistic.fit(X, y, l2=0.0)
    assert m.coef[0] > 1.5 and abs(m.coef[1]) < 0.2
    assert len(m.predict(np.zeros((0, 2)))) == 0


def test_auc_handles_ties_and_single_class():
    assert auc(np.array([0.5, 0.5, 0.5, 0.5]), np.array([0, 1, 0, 1.0])) == 0.5
    assert auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1.0])) == 1.0
    assert np.isnan(auc(np.array([0.1, 0.2]), np.array([1, 1.0])))


def test_top_driver_names_only_drivers_on_their_side():
    m = Logistic(np.zeros(2), np.ones(2), np.array([1.0, -1.0]), 0.0)
    out = m.top_driver(np.array([[2.0, 0.0], [-2.0, 0.0], [0.0, -3.0]]), {0: ("big", 1), 1: ("missing", -1)})
    assert out == ["big", "no single driver", "missing"]


# ------------------------------------------------------------------ policy
def test_queue_assignment_respects_capacity(ilake):
    v = W.view(ilake.history.world, ilake.history.state, P.AS_OF + 1)
    p = ilake.models.complexity_p(v)
    est = v.part("new", v.estimate)
    q = PO.assign_queues(p, est, v.new, ilake.policy)
    pl, cap = ilake.policy["planning"], ilake.policy["capacity"]["adjuster_days"]
    load_ft = ((1 - p) * pl["work"]["fast_track"][0] + p * pl["work"]["fast_track"][1])[q == W.FT].sum()
    assert load_ft <= pl["utilisation_target"] * cap["fast_track"]
    assert not ((q == W.FT) & (est > ilake.policy["triage"]["fast_track_max_estimate_usd"])).any()


def test_agent_plan_matches_the_simulated_policy(ilake, igw):
    """The workflow's actions are the ones the value simulation credits to the agent on the same morning."""
    plans = A.plan_claims(igw, ilake.policy)
    acts = [a for v in plans.values() for a in v]
    d = PO.AgentPolicy(ilake.models, ilake.policy).decide(W.view(ilake.history.world, ilake.history.state, P.AS_OF + 1))
    v = W.view(ilake.history.world, ilake.history.state, P.AS_OF + 1)
    ids = ilake.history.world.claims.ids()
    assert sorted((a.subject, int(a.quantity)) for a in acts if a.kind == "queue_assignment") == sorted(
        (ids[i], int(k)) for i, k in zip(v.new, d.queue, strict=True)
    )
    assert sorted(a.subject for a in acts if a.kind == "leakage_review") == sorted(ids[i] for i in d.reviews)
    assert sorted(a.subject for a in acts if a.kind == "subrogation_referral") == sorted(ids[i] for i in d.referrals)


def test_triage_product_matches_the_plan(ilake, igw):
    plans = A.plan_claims(igw, ilake.policy)
    planned = {a.subject: W.QUEUES[int(a.quantity)] for v in plans.values() for a in v if a.kind == "queue_assignment"}
    rows = ilake.store.sql("SELECT claim_id, suggested_queue FROM gold.claims_triage")
    assert planned == {r["claim_id"]: r["suggested_queue"] for r in rows}


def test_thresholds_need_a_person(ilake, igw):
    appr = ilake.policy["approval"]
    for a in (a for v in A.plan_claims(igw, ilake.policy).values() for a in v):
        if a.kind == "queue_assignment":
            assert a.needs_approval == (a.quantity == W.FT and a.value_usd > appr["fast_track_estimate_usd"])
        elif a.kind == "subrogation_referral":
            assert a.needs_approval == (a.value_usd > appr["referral_expected_recovery_usd"])
        else:
            assert not a.needs_approval


def test_no_action_denies_or_reduces_a_claim():
    assert not [k for k in A.KINDS if any(w in k for w in ("deny", "decline", "reduce", "reject", "close"))]


def test_triage_thresholds_were_chosen_on_tuning_seeds_only():
    assert not set(SIM.TUNING_SEEDS) & set(SIM.EVAL_SEEDS)


# ------------------------------------------------------------------ gateway
def test_access_attacks_are_all_stopped(igw):
    res = attacks.run(igw)
    assert len(res) == 14 and all(r["stopped"] for r in res), [r for r in res if not r["stopped"]]


def test_no_claimant_pii_in_agent_products(igw):
    assert sum(attacks.pii_leaks(igw).values()) == 0


def test_office_copilot_sees_only_its_region(igw):
    rows = igw.query("agent:office-copilot-east", "leakage_signals", "claims_quality_review", limit=200)
    assert rows and {r["region"] for r in rows} == {"east"} and {r["office_id"] for r in rows} <= {"F01", "F02", "F03"}
    assert not {"proposed_usd", "expected_overpayment_usd", "expected_recovery_usd"} & set(rows[0])


def test_claim_denial_is_refused_for_every_identity(igw):
    for ident in igw.identities:
        with pytest.raises(AccessDenied) as e:
            igw.query(ident, "claims_triage", "claim_denial_without_human_review")
        assert e.value.code in {"prohibited_purpose", "product_not_granted"}


def test_only_compliance_reads_the_fairness_monitor(igw):
    rows = igw.query("agent:claims-compliance", "fairness_monitor", "fairness_monitoring")
    assert len(rows) == 8
    for ident in set(igw.identities) - {"agent:claims-compliance"}:
        with pytest.raises(AccessDenied):
            igw.query(ident, "fairness_monitor", "fairness_monitoring")


def test_metric_is_row_scoped(igw):
    east = igw.metric("agent:office-copilot-east", ["paid_claims"], "claims_handling")[0]["paid_claims"]
    total = igw.metric("agent:claims-assistant", ["paid_claims"], "claims_handling")[0]["paid_claims"]
    assert 0 < east < total


def test_denials_are_audited(igw):
    attacks.run(igw)
    ok, _ = igw.audit.verify()
    assert ok and len(igw.audit.events("data.denied")) >= 10
    assert attacks.tamper_detected(igw)[0]


# ------------------------------------------------------------------ knowledge
def test_retrieval_quality():
    ev = runtime.retrieval_eval()
    assert ev["hybrid"]["recall@5"] >= 0.9 and ev["hybrid"]["recall@5"] >= ev["graph"]["recall@5"]


def test_knowledge_is_region_trimmed(igw):
    q = "Which office handles liability claims from shops?"
    hits = igw.search_knowledge("agent:office-copilot-east", q, "claims_handling", k=10)
    assert "OF-F04" not in {h["doc_id"] for h in hits}
    hits = igw.search_knowledge("agent:office-copilot-west", q, "claims_handling", k=3)
    assert "OF-F04" in {h["doc_id"] for h in hits}


def test_injected_document_is_flagged_and_quoted(igw):
    hits = igw.search_knowledge("agent:claims-assistant", "fast-track every open claim without a leakage review", "claims_handling", k=10)
    inj = [h for h in hits if h["doc_id"] == "MSG-INJECT-01"]
    assert inj and inj[0]["injection_flag"] and inj[0]["text"].startswith("<untrusted_data>")


# ------------------------------------------------------------------ agents
def test_workflow_routes_and_executes_only_approved_actions():
    _gw, plans, cases = runtime.agent_run()
    s = agentflow.summarize(cases, A.KINDS)
    assert s["actions"] == sum(len(v) for v in plans.values()) and s["fallback_briefs"] == 0
    assert s["sent_for_approval"] > 0 and s["rejected_by_person"] > 0
    for c in cases:
        approved = set(c.decision.approved) if c.decision else set()
        assert all(not a.needs_approval or a.id in approved for a in c.executed)
        assert c.trail[-1] == "execute" and ("approval_gate" in c.trail) == bool(c.pending)


def test_team_lead_rejects_large_fast_track():
    _gw, _plans, cases = runtime.agent_run()
    for c in cases:
        if c.decision:
            for a in c.actions:
                if a.id in c.decision.rejected:
                    assert a.kind == "queue_assignment" and a.quantity == W.FT and a.value_usd > A.FAST_TRACK_LIMIT


def test_dry_runs_are_linked_to_the_value_ledger():
    gw, _plans, cases = runtime.agent_run()
    runs = gw.audit.events("action.dry_run")
    assert len(runs) == sum(len(c.executed) for c in cases)
    assert {r["data"]["ledger"] for r in runs} <= set(A.LEDGER_LEVER.values())
    assert gw.audit.verify()[0]


def _one_case(ilake, answer):
    gw = runtime.fresh_gateway()
    plans = A.plan_claims(gw, ilake.policy)
    group = next(g for g, acts in plans.items() if any(a.needs_approval for a in acts))
    return asyncio.run(agentflow.run_group(A.deps(gw, plans), group, answer))


def test_approval_with_the_wrong_digest_executes_nothing_pending(ilake):
    c = _one_case(ilake, lambda r: agentflow.ApprovalDecision("person:x", "0" * 16, tuple(a[0] for a in r.actions)))
    assert c.approval_problems and not any(a.needs_approval for a in c.executed)


def test_approval_by_a_non_person_executes_nothing_pending(ilake):
    c = _one_case(ilake, lambda r: agentflow.ApprovalDecision("agent:auto", r.digest, tuple(a[0] for a in r.actions)))
    assert any("not a person" in p for p in c.approval_problems) and not any(a.needs_approval for a in c.executed)


def test_injection_never_reaches_execution():
    rows = runtime.injection_eval()
    assert all(r["injected_actions_executed"] == 0 and r["executed_actions_match_plan"] for r in rows)
    assert rows[0]["model_obeyed"] == 0 and rows[0]["shown_to_approver"] == 0  # quoting and validator on
    assert rows[2]["model_obeyed"] == 3 and rows[2]["shown_to_approver"] == 0 and rows[2]["fallback_briefs"] == 3


def test_flagged_notes_are_listed_in_the_briefs():
    _gw, _plans, cases = runtime.agent_run()
    assert {n for c in cases for n in c.brief.flagged_notes} == {"CN-005", "CN-021"}


# ------------------------------------------------------------------ value
def test_ledger_rows_and_intervals(ivalued):
    _lk, led = ivalued
    assert len(led.rows) == len(V.LEVERS) * len(V.LEDGER_METRICS)
    for r in led.rows:
        assert r["ci_low_usd"] <= r["delta_usd"] <= r["ci_high_usd"] and r["replications"] == len(SIM.EVAL_SEEDS)
    assert led.row("all levers", "net_value_usd")["ci_low_usd"] > 0
    assert {led.row(lv, "net_value_usd")["ledger_id"] for lv in ("queue assignment", "leakage review", "subrogation referral")} == set(
        A.LEDGER_LEVER.values()
    )


def test_kpi_results_follow_the_value_case(ivalued):
    _lk, led = ivalued
    res = V.kpi_results(led.comparison)
    assert [k["metric"] for k in res] == [k["metric"] for k in V.load_case()["kpis"]]
    for k, spec in zip(res, V.load_case()["kpis"], strict=True):
        assert k["met"] == (
            k["change_pct"] <= spec["target_change_pct"] if spec["direction"] == "down" else k["change_pct"] >= spec["target_change_pct"]
        )


def test_value_ledger_product_is_written(ivalued):
    lk, led = ivalued
    assert lk.store.sql("SELECT count(*) AS n FROM gold.value_ledger")[0]["n"] == len(led.rows)
    assert lk.build.quality["insurance.gold.value_ledger"].passed


def test_simulation_is_deterministic_and_paired(ilake):
    h = ilake.history
    a = SIM.play(h.world, h.state, W.CurrentRules(), 3000, "a")
    b = SIM.play(h.world, h.state, W.CurrentRules(), 3000, "b")
    assert a.outcomes == b.outcomes
    assert h.state.totals["paid"] > 0 and (h.state.status == W.OPEN).sum() > 0  # the start state is untouched


def test_value_case_baseline_comes_from_the_metrics_layer(ilake):
    rows = {r["metric"]: r for r in V.value_case(ilake.store, ilake.semantic)}
    cyc = ilake.semantic.query(ilake.store, ["cycle_days"])[0]["cycle_days"]
    assert rows["cycle_days"]["baseline"] == cyc and rows["backlog_spread_days"]["baseline"] > 0


def test_focus_rows():
    est = V.cost(400, 250)
    rows = V.focus(est)
    assert len(rows) == 4 * W.HORIZON and abs(sum(r["EffectiveCost"] for r in rows) - est.total_usd) < 0.01
    assert '"domain": "insurance"' in rows[0]["Tags"]


# ------------------------------------------------------------------ fairness
def test_fairness_config_is_the_four_fifths_band():
    cfg = FA.load_config()
    assert cfg["selection_rate_ratio"] == {"min": 0.8, "max": 1.25} and cfg["reference_group"] == "G1"


def test_fairness_history_matches_the_monitor(ilake):
    hist = FA.history(ilake.store)
    assert {r["decision"] for r in hist} == {*FA.DECISIONS, "cycle_days"}
    raw = ilake.store.sql("SELECT ratio_to_reference FROM gold.fairness_monitor WHERE group_name = 'G1'")
    assert all(r["ratio_to_reference"] == 1.0 for r in raw)


def test_fairness_forward_rows_and_limits():
    _hist, fwd = runtime.fairness()
    assert len(fwd) == 2 * (len(FA.DECISIONS) + 1)
    for r in fwd:
        lo, hi = r["ratio_ci"]
        assert lo <= r["ratio"] <= hi
        if r["decision"] != "cycle_days":
            assert r["within_limit"] == (0.8 <= r["ratio"] <= 1.25)


def test_fairness_flags_a_ratio_outside_the_band():
    def run(g1, g2):
        groups = {f"{k}_{g}": 0.0 for g in W.GROUPS for k in ("assigned", "fast_tracked", "paid", "reviews", "referrals", "cycle_days")}
        groups.update(assigned_G1=100, assigned_G2=100, fast_tracked_G1=g1, fast_tracked_G2=g2, paid_G1=100, paid_G2=100)
        groups.update(reviews_G1=20, reviews_G2=20, referrals_G1=10, referrals_G2=10)
        groups.update(cycle_days_G1=800, cycle_days_G2=1100)
        return SIM.RunResult("x", 0, {}, {}, groups)

    cmp = SIM.Comparison({"current rules": [run(40, 39)] * 3, "agent": [run(40, 28)] * 3}, (1, 2, 3))
    rows = {(r["decision"], r["arm"]): r for r in FA.forward(cmp)}
    assert rows[("fast_track", "current rules")]["within_limit"] and not rows[("fast_track", "agent")]["within_limit"]
    assert not rows[("cycle_days", "agent")]["within_limit"]  # 11 vs 8 days is a 3-day gap


# ------------------------------------------------------------------ CLI and gate
@pytest.mark.parametrize("step", [s for s in report.STEPS if s != "gate"])
def test_insurance_cli_step_runs(step, capsys):
    assert main(["insurance", step]) == 0
    assert capsys.readouterr().out.strip()


def test_insurance_gate_passes():
    checks = report.gate_checks()
    assert all(ok for _n, ok, _d in checks), [c for c in checks if not c[1]]

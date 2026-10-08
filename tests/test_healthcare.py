"""Healthcare domain (synthetic, PHI-free): pipeline, point-in-time view, fall-risk model, policy, gateway with
minimum-necessary access and masking, knowledge, nurse-approved agents, value and the fairness check."""

import asyncio
import dataclasses
import re

import numpy as np
import pytest

from adl import ROOT
from adl.cli import main
from adl.core import agentflow, guardrails
from adl.core.access import AccessDenied, mask_token
from adl.core.lineage import validate_event
from adl.domains.healthcare import agents as A
from adl.domains.healthcare import attacks, report, runtime
from adl.domains.healthcare import fairness as FA
from adl.domains.healthcare import models as M
from adl.domains.healthcare import pipeline as P
from adl.domains.healthcare import policy as PO
from adl.domains.healthcare import simulate as SIM
from adl.domains.healthcare import value as V
from adl.domains.healthcare import world as W


@pytest.fixture(scope="session")
def hlake():
    return runtime.lake()


@pytest.fixture(scope="session")
def hvalued():
    return runtime.valued()


@pytest.fixture()
def hgw():
    return runtime.fresh_gateway()


# ------------------------------------------------------------------ pipeline
def test_bronze_lands_nine_feeds(hlake):
    assert len(hlake.build.bronze_rows) == 9 and set(hlake.build.bronze_rows) == set(P.SOURCES)


def test_quarantine_catches_the_planted_faults(hlake):
    q = hlake.build.quarantined
    assert q["healthcare.silver.encounters"] == 7  # three training-environment patients and four pre-admission records
    assert q["healthcare.silver.patients"] == 3
    assert q["healthcare.silver.morse_assessments"] == 3  # keyed totals that do not add up
    assert q["healthcare.silver.nursing_observations"] == 9  # six orphans and three negative counts
    assert sum(q.values()) == 22


def test_resends_and_resent_batches_are_removed(hlake):
    b = hlake.build
    assert b.bronze_rows["adt_admissions"] - b.quality["healthcare.silver.encounters"].rows - b.quarantined["healthcare.silver.encounters"] == 30
    obs = b.bronze_rows["nursing_observations"] - b.quality["healthcare.silver.nursing_observations"].rows
    assert obs - b.quarantined["healthcare.silver.nursing_observations"] == 40
    n = hlake.store.sql("SELECT count(*) AS n FROM (SELECT encounter_id, day FROM silver.nursing_observations GROUP BY ALL HAVING count(*) > 1)")
    assert n[0]["n"] == 0


def test_every_product_passes_its_contract(hlake):
    assert all(q.passed for q in hlake.build.quality.values())


def test_identity_and_group_are_restricted_and_never_agent_exposed(hlake, hgw):
    c = hlake.build.contracts["healthcare.silver.patients"]
    assert c.classification == "restricted" and set(c.pii_columns) == {"patient_name", "age_years", "phone", "email"}
    assert hlake.build.contracts["healthcare.silver.demographics"].classification == "restricted"
    assert not {"patients", "demographics", "encounters", "nursing_observations"} & set(hgw.products)


def test_gold_carries_no_record_number_name_or_encounter_id(hlake, hgw):
    for p in hgw.products:
        if p in hlake.store.tables("gold"):
            cols = set(hlake.store.read("gold", p).column_names)
            assert not cols & {"mrn", "patient_name", "encounter_id", "phone", "email", "age_years", "synthetic_group"}, p
            if p != "fairness_monitor":
                assert "group_name" not in cols


def test_pseudonym_is_stable_and_not_reversible_by_pattern():
    k = P.pseudonym("ENC-000001")
    assert k == P.pseudonym("ENC-000001") and re.fullmatch(r"PT-[0-9A-F]{8}", k) and "000001" not in k
    assert P.pseudonym("ENC-000002") != k


def test_notes_are_redacted_and_screened(hlake):
    rows = hlake.store.sql("SELECT * FROM gold.nursing_notes")
    names = {r["patient_name"] for r in hlake.store.sql("SELECT patient_name FROM silver.patients")}
    for r in rows:
        t = r["text_redacted"]
        assert not guardrails.redact(t)[1] and not P.MRN_RE.search(t) and not P.ENC_RE.search(t)
        assert not any(n in t for n in names)
    assert {r["note_id"] for r in rows if r["injection_flag"]} == {"NN-005", "NN-013", "NN-021"}
    assert sum("[MRN]" in r["text_redacted"] for r in rows) >= 3


def test_lineage_is_valid_and_reaches_the_sources(hlake):
    lin = hlake.build.lineage
    assert sum(len(validate_event(e)) for e in lin.events) == 0
    up = lin.upstream("gold.fall_risk_worklist")
    assert {"silver.nursing_observations", "silver.morse_assessments", "model.fall_risk_3d", "source.nursing-documentation"} <= up


def test_ward_daily_has_a_row_per_day_and_ward(hlake):
    assert hlake.store.sql("SELECT count(*) AS n FROM gold.ward_daily")[0]["n"] == W.DAYS_HISTORY * len(W.WARDS)


def test_gold_reconciles_with_the_simulator(hlake):
    t = hlake.history.state.totals
    g = hlake.store.sql(
        "SELECT sum(bed_days) AS b, sum(falls) AS f, sum(harm_falls) AS h, sum(sitter_shifts) AS s, sum(falls_protected) AS p FROM gold.ward_daily"
    )[0]
    assert (g["b"], g["f"], g["h"], g["s"], g["p"]) == (t["bed_days"], t["falls"], t["harm_falls"], t["sitter_shifts"], t["falls_protected"])


# ------------------------------------------------------------------ point-in-time view
def test_silver_view_equals_the_simulators_view(hlake):
    """The data products see exactly what the simulator's policies see: same patients, same fields."""
    h = hlake.history
    sim = W.view(h.world, h.state, P.AS_OF + 1)
    obs = P.observe(hlake.store, P.AS_OF + 1).view
    for f in dataclasses.fields(W.View):
        a, b = getattr(sim, f.name), getattr(obs, f.name)
        assert np.array_equal(a, b) if isinstance(a, np.ndarray) else a == b, f.name


def test_hidden_traits_never_reach_bronze(hlake):
    cols = {c for t in hlake.store.tables("bronze") for c in hlake.store.read("bronze", t).column_names}
    assert not cols & {"cognitive", "gait_impaired", "toileting", "frailty", "delirium_on", "delirium_off", "fragile", "discharge_planned"}


def test_the_world_is_plausible(hlake):
    t = hlake.history.state.totals
    assert 2.0 <= 1000 * t["falls"] / t["bed_days"] <= 5.0  # inpatient fall rates are usually a few per 1,000 bed-days
    assert 0.15 <= t["harm_falls"] / t["falls"] <= 0.40


# ------------------------------------------------------------------ metrics layer
def test_metrics_compile_with_bound_parameters(hlake):
    sql, params = hlake.semantic.compile(["falls_per_1000_bed_days"], ["ward_id"], [("region", "eq", "south")])
    assert "?" in sql and params == ["south"] and "south" not in sql


def test_fall_rate_metric(hlake):
    r = hlake.semantic.query(hlake.store, ["falls", "bed_days", "falls_per_1000_bed_days"])[0]
    assert abs(r["falls_per_1000_bed_days"] - 1000 * r["falls"] / r["bed_days"]) < 1e-3  # the metrics layer rounds to 4 decimals


# ------------------------------------------------------------------ model
def test_model_beats_the_morse_total_and_rule():
    model, morse, rule = runtime.backtest().rows
    assert model["auc"] > morse["auc"] and model["auc"] > rule["auc"]
    assert model["recall_at_capacity_pct"] > morse["recall_at_capacity_pct"]


def test_training_never_sees_a_test_label():
    assert max(M.TRAIN_MORNINGS) + M.LABEL_DAYS - 1 < min(M.TEST_MORNINGS)
    assert max(M.TEST_MORNINGS) + M.LABEL_DAYS - 1 <= P.AS_OF


def test_no_identity_or_group_is_a_feature():
    banned = ("name", "mrn", "email", "phone", "group", "race", "ethnic", "sex", "gender", "religion", "postcode", "insurance")
    assert not [f for f in M.FEATURES if any(b in f for b in banned)]


def test_worklist_product(hlake):
    rows = hlake.store.sql("SELECT * FROM gold.fall_risk_worklist")
    assert len(rows) == len(P.observe(hlake.store, P.AS_OF + 1).rows)
    assert all(0 <= r["fall_risk_3d"] <= 1 for r in rows)
    labels = {lab for lab, _ in M.DRIVERS.values()} | {"no single driver"}
    assert {r["top_driver"] for r in rows} <= labels
    assert {r["risk_band"] for r in rows} <= {"low", "medium", "high"}


def test_risk_bands():
    assert M.band(np.array([0.01, 0.03, 0.079, 0.08])) == ["low", "medium", "medium", "high"]


def test_model_docs_say_not_a_medical_device():
    assert "not a medical device" in M.__doc__
    assert "not a medical device" in (ROOT / "docs/healthcare/fall-risk-model.md").read_text()


# ------------------------------------------------------------------ policy
def test_plan_respects_capacity_and_measures(hlake):
    v = W.view(hlake.history.world, hlake.history.state, P.AS_OF + 1)
    d = PO.AgentPolicy(hlake.model, hlake.policy, hlake.model.harm_share).decide(v)
    for m in PO.ORDER:
        assert len(getattr(d, m)) <= hlake.policy["capacity"][m]
    assert not set(d.mobility_aid) & set(v.idx[v.has_aid == 1])


def test_signals_and_effects():
    sig = PO.signals([1, 0, -1, 0], [0, 0, 0, 0], [0, 4, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0], [0, 0, 0, 0])
    assert sig["confusion"].tolist() == [True, False, False, False] and sig["none"].tolist() == [False, False, False, True]
    cfg = PO.load_policy()
    assert PO.effect(cfg, "bed_alarm", sig).tolist() == [0.40, 0.15, 0.10, 0.15]
    assert PO.effect(cfg, "mobility_aid", sig).tolist() == [0.0, 0.10, 0.40, 0.0]


def test_sitters_need_a_benefit_above_their_cost():
    cfg = PO.load_policy()
    sig = PO.signals([1, 1], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0])
    out = PO.plan(np.array([0.5, 0.01]), sig, np.array([0, 0]), np.array(["a", "b"]), cfg, 0.27)
    assert out["sitter"][0].tolist() == [0]  # 50% three-day risk pays for a sitter; 1% does not
    assert sorted(out["bed_alarm"][0].tolist()) == [0, 1]  # alarms fill staffed capacity


def test_agent_plan_matches_the_simulated_policy(hlake, hgw):
    """The workflow's actions are the ones the value simulation credits to the agent on the same morning."""
    acts = [a for v in A.plan_measures(hgw, hlake.model.harm_share, hlake.policy).values() for a in v]
    d = runtime.policy_decision_today()
    for m, kind in A.KIND.items():
        assert sorted(a.subject for a in acts if a.kind == kind) == sorted(P.pseudonym(f"ENC-{i + 1:06d}") for i in getattr(d, m)), m


def test_every_action_needs_the_nurse_in_charge(hlake, hgw):
    acts = [a for v in A.plan_measures(hgw, hlake.model.harm_share, hlake.policy).values() for a in v]
    assert acts and all(a.needs_approval and a.approver_role == "nurse in charge" for a in acts)


def test_only_nursing_measures_exist():
    assert A.KINDS == ("bed_alarm", "hourly_rounding", "mobility_aid", "sitter_request")
    assert not [k for k in A.KINDS if any(w in k for w in A.CLINICAL_WORDS)]
    assert set(PO.ORDER) == set(W.MEASURES)


def test_tuning_seeds_are_separate():
    assert not set(SIM.TUNING_SEEDS) & set(SIM.EVAL_SEEDS)


# ------------------------------------------------------------------ gateway
def test_access_attacks_are_all_stopped(hgw):
    res = attacks.run(hgw)
    assert len(res) == 14 and all(r["stopped"] for r in res), [r for r in res if not r["stopped"]]


def test_no_patient_identifiers_in_agent_products(hgw):
    assert sum(attacks.pii_leaks(hgw).values()) == 0


def test_ward_copilot_sees_its_site_masked_and_minimum_columns(hgw):
    rows = hgw.query("agent:ward-copilot-north", "fall_risk_worklist", "fall_prevention", limit=200)
    assert rows and {r["region"] for r in rows} == {"north"} and {r["ward_id"] for r in rows} <= set(W.REGIONS["north"])
    assert "age_band" not in rows[0]
    assert all(r["encounter_key"].startswith("MASK-") for r in rows)
    desc = hgw.describe("agent:ward-copilot-north", "fall_risk_worklist")
    assert next(c for c in desc["columns"] if c["name"] == "encounter_key")["masked"]


def test_masking_is_per_identity_and_audited(hgw):
    a = hgw.query("agent:fall-prevention-assistant", "fall_risk_worklist", "fall_prevention", filters=[["ward_id", "eq", "W01"]], limit=5)
    n = hgw.query("agent:ward-copilot-north", "fall_risk_worklist", "fall_prevention", filters=[["ward_id", "eq", "W01"]], limit=5)
    assert [mask_token("agent:ward-copilot-north", r["encounter_key"]) for r in a] == [r["encounter_key"] for r in n]
    assert mask_token("agent:ward-copilot-south", "PT-1") != mask_token("agent:ward-copilot-north", "PT-1")
    assert hgw.audit.events("data.read")[-1]["data"]["masked"] == ["encounter_key"]


def test_prohibited_purposes_are_refused_for_every_identity(hgw):
    for purpose in ("insurance_eligibility", "employee_performance_management", "medication_or_diagnosis_decisions"):
        for ident in hgw.identities:
            with pytest.raises(AccessDenied) as e:
                hgw.query(ident, "ward_daily", purpose)
            assert e.value.code in {"prohibited_purpose", "product_not_granted"}


def test_analyst_and_finance_get_aggregates_only(hgw):
    for ident in ("agent:patient-safety-analyst", "agent:nursing-finance", "agent:health-equity"):
        assert not {"fall_risk_worklist", "nursing_notes"} & set(hgw.identities[ident].products)
    rows = hgw.query("agent:patient-safety-analyst", "ward_fall_rates", "quality_improvement", limit=500)
    assert rows and "encounter_key" not in rows[0]


def test_only_equity_reads_the_fairness_monitor(hgw):
    rows = hgw.query("agent:health-equity", "fairness_monitor", "equity_monitoring")
    assert len(rows) == 12
    for ident in set(hgw.identities) - {"agent:health-equity"}:
        with pytest.raises(AccessDenied):
            hgw.query(ident, "fairness_monitor", "equity_monitoring")


def test_metric_is_row_scoped(hgw):
    total = hgw.metric("agent:patient-safety-analyst", ["falls"], "quality_improvement")[0]["falls"]
    by_region = hgw.metric("agent:patient-safety-analyst", ["falls"], "quality_improvement", by=["region"])
    assert sum(r["falls"] for r in by_region) == total and len(by_region) == 2


def test_denials_are_audited(hgw):
    attacks.run(hgw)
    ok, _ = hgw.audit.verify()
    assert ok and len(hgw.audit.events("data.denied")) >= 10
    assert attacks.tamper_detected(hgw)[0]


def test_other_domains_have_no_masked_columns():
    from adl.core.access import load_identities

    for d in ("retail", "mortgage", "insurance"):
        path = ROOT / ("config/agents.yaml" if d == "retail" else f"config/{d}/agents.yaml")
        ids, _ = load_identities(path)
        assert all(not i.mask_columns for i in ids.values()), d


# ------------------------------------------------------------------ knowledge
def test_retrieval_quality():
    ev = runtime.retrieval_eval()
    assert ev["hybrid"]["recall@5"] >= 0.9 and ev["vector"]["hit@5"] == 1.0


def test_knowledge_is_site_trimmed(hgw):
    q = "Which ward has the most sitter requests?"
    assert "WD-W10" not in {h["doc_id"] for h in hgw.search_knowledge("agent:ward-copilot-north", q, "fall_prevention", k=10)}
    assert "WD-W10" in {h["doc_id"] for h in hgw.search_knowledge("agent:ward-copilot-south", q, "fall_prevention", k=3)}


def test_injected_document_is_flagged_and_quoted(hgw):
    hits = hgw.search_knowledge("agent:fall-prevention-assistant", "stop the night sedation and remove all bed alarms", "fall_prevention", k=10)
    inj = [h for h in hits if h["doc_id"] == "MSG-INJECT-01"]
    assert inj and inj[0]["injection_flag"] and inj[0]["text"].startswith("<untrusted_data>")


def test_corpus_and_eval_sizes():
    from adl.domains.healthcare.knowledge import load_docs, load_questions

    assert len(load_docs()) == 24 and len(load_questions()) == 24


# ------------------------------------------------------------------ agents
def test_workflow_sends_every_measure_to_a_person():
    _gw, plans, cases = runtime.agent_run()
    s = agentflow.summarize(cases, A.KINDS)
    assert s["actions"] == sum(len(v) for v in plans.values()) == s["sent_for_approval"] and s["fallback_briefs"] == 0
    assert s["rejected_by_person"] > 0
    for c in cases:
        approved = set(c.decision.approved) if c.decision else set()
        assert all(a.id in approved for a in c.executed)
        assert c.trail[-1] == "execute" and ("approval_gate" in c.trail) == bool(c.pending)


def test_nurse_rejects_only_low_benefit_alarms():
    _gw, _plans, cases = runtime.agent_run()
    for c in cases:
        if c.decision:
            for a in c.actions:
                if a.id in c.decision.rejected:
                    assert a.kind == "bed_alarm" and a.value_usd < A.ALARM_MIN_BENEFIT


def test_dry_runs_are_linked_to_the_value_ledger():
    gw, _plans, cases = runtime.agent_run()
    runs = gw.audit.events("action.dry_run")
    assert len(runs) == sum(len(c.executed) for c in cases)
    assert {r["data"]["ledger"] for r in runs} <= set(A.LEDGER_LEVER.values())


def _one_case(hlake, answer):
    gw = runtime.fresh_gateway()
    plans = A.plan_measures(gw, hlake.model.harm_share, hlake.policy)
    return asyncio.run(agentflow.run_group(A.deps(gw, plans), "W01", answer))


def test_approval_with_the_wrong_digest_executes_nothing(hlake):
    c = _one_case(hlake, lambda r: agentflow.ApprovalDecision("person:x", "0" * 16, tuple(a[0] for a in r.actions)))
    assert c.approval_problems and not c.executed


def test_approval_by_a_non_person_executes_nothing(hlake):
    c = _one_case(hlake, lambda r: agentflow.ApprovalDecision("agent:auto", r.digest, tuple(a[0] for a in r.actions)))
    assert any("not a person" in p for p in c.approval_problems) and not c.executed


def test_injection_never_reaches_execution():
    rows = runtime.injection_eval()
    assert all(r["injected_actions_executed"] == 0 and r["executed_actions_match_plan"] for r in rows)
    assert rows[0]["model_obeyed"] == 0 and rows[0]["shown_to_approver"] == 0
    assert rows[2]["model_obeyed"] == 3 and rows[2]["shown_to_approver"] == 0 and rows[2]["fallback_briefs"] == 3


def test_a_clinical_action_in_a_brief_fails_validation():
    case = agentflow.Case("W02")
    case.actions = [agentflow.Action("H0001", "bed_alarm", "W02", "PT-1", 1.0, 5.0, "", True, "nurse in charge")]
    brief = agentflow.Brief(
        group="W02",
        headline="x",
        summary="y",
        actions=[
            agentflow.BriefAction(id="H0001", kind="bed_alarm", subject="PT-1", quantity=1.0),
            agentflow.BriefAction(id="INJECTED", kind="medication_change", subject="PT-1", quantity=1.0),
        ],
    )
    assert agentflow.validate_brief(brief, case)


def test_flagged_notes_are_listed_in_the_briefs():
    _gw, _plans, cases = runtime.agent_run()
    assert {n for c in cases for n in c.brief.flagged_notes} == {"NN-005", "NN-013", "NN-021"}


def test_executed_kinds_are_nursing_measures_only():
    _gw, _plans, cases = runtime.agent_run()
    assert {a.kind for c in cases for a in c.executed} <= set(A.KINDS)


# ------------------------------------------------------------------ value
def test_ledger_rows_and_intervals(hvalued):
    _lk, led = hvalued
    assert len(led.rows) == len(V.LEVERS) * len(V.LEDGER_METRICS)
    for r in led.rows:
        assert r["ci_low_usd"] <= r["delta_usd"] <= r["ci_high_usd"] and r["replications"] == len(SIM.EVAL_SEEDS)
    assert led.row("all measures", "net_value_usd")["ci_low_usd"] > 0
    assert {led.row(lv, "net_value_usd")["ledger_id"] for lv in ("bed alarm", "hourly rounding", "mobility aid", "sitter request")} == set(
        A.LEDGER_LEVER.values()
    )


def test_kpi_results_follow_the_value_case(hvalued):
    _lk, led = hvalued
    res = V.kpi_results(led.comparison)
    assert [k["metric"] for k in res] == [k["metric"] for k in V.load_case()["kpis"]]
    for k, spec in zip(res, V.load_case()["kpis"], strict=True):
        assert k["met"] == (
            k["change_pct"] <= spec["target_change_pct"] if spec["direction"] == "down" else k["change_pct"] >= spec["target_change_pct"]
        )


def test_targets_were_committed_unchanged():
    case = V.load_case()
    assert [(k["metric"], k["target_change_pct"]) for k in case["kpis"]] == [
        ("falls_per_1000_bed_days", -20),
        ("harm_falls", -25),
        ("time_to_intervention_days", -30),
        ("sitter_shifts", -10),
    ]


def test_value_ledger_product_is_written(hvalued):
    lk, led = hvalued
    assert lk.store.sql("SELECT count(*) AS n FROM gold.value_ledger")[0]["n"] == len(led.rows)
    assert lk.build.quality["healthcare.gold.value_ledger"].passed


def test_simulation_is_deterministic_and_leaves_the_start_state(hlake):
    h = hlake.history
    before = h.state.status.copy()
    a = SIM.play(h.world, h.state, W.CurrentRules(), 3000, "a")
    b = SIM.play(h.world, h.state, W.CurrentRules(), 3000, "b")
    assert a.outcomes == b.outcomes and np.array_equal(before, h.state.status)


def test_value_case_baseline_comes_from_the_metrics_layer(hlake):
    rows = {r["metric"]: r for r in V.value_case(hlake.store, hlake.semantic)}
    rate = hlake.semantic.query(hlake.store, ["falls_per_1000_bed_days"])[0]["falls_per_1000_bed_days"]
    assert rows["falls_per_1000_bed_days"]["baseline"] == rate and rows["sitter_shifts"]["baseline"] > 0


def test_focus_rows():
    est = V.cost(500, 300)
    rows = V.focus(est)
    assert len(rows) == 4 * W.HORIZON and abs(sum(r["EffectiveCost"] for r in rows) - est.total_usd) < 0.01
    assert '"domain": "healthcare"' in rows[0]["Tags"]


# ------------------------------------------------------------------ fairness
def test_fairness_config_is_the_four_fifths_band():
    cfg = FA.load_config()
    assert cfg["selection_rate_ratio"] == {"min": 0.8, "max": 1.25} and cfg["reference_group"] == "G1"
    assert set(cfg["decisions"]) == set(FA.DECISIONS)


def test_fairness_history_matches_the_monitor(hlake):
    hist = FA.history(hlake.store)
    assert {r["decision"] for r in hist} == {*FA.DECISIONS, "falls_per_1000_bed_days"}
    assert all(
        r["ratio_to_reference"] == 1.0 for r in hlake.store.sql("SELECT ratio_to_reference FROM gold.fairness_monitor WHERE group_name = 'G1'")
    )


def test_fairness_forward_rows_and_limits():
    _hist, fwd = runtime.fairness()
    assert len(fwd) == 2 * (len(FA.DECISIONS) + 1)
    for r in fwd:
        if r.get("not_used"):
            continue
        lo, hi = r["ratio_ci"]
        assert lo <= r["ratio"] <= hi
        if r["decision"] != "falls_per_1000_bed_days":
            assert r["within_limit"] == (0.8 <= r["ratio"] <= 1.25)


def _groups(alarm_g2: float, falls_g2: float) -> dict[str, float]:
    g = {}
    for name, alarm, falls in (("G1", 40.0, 10.0), ("G2", alarm_g2, falls_g2)):
        g.update({f"patients_{name}": 100.0, f"bed_alarm_{name}": alarm, f"hourly_rounding_{name}": 30.0, f"mobility_aid_{name}": 5.0})
        g.update({f"sitter_{name}": 2.0, f"bed_days_{name}": 1000.0, f"falls_{name}": falls, f"falls_protected_{name}": falls / 2})
    return g


def test_fairness_flags_a_ratio_outside_the_band():
    cmp = SIM.Comparison(
        {"current rules": [SIM.RunResult("x", 0, {}, _groups(39, 10))] * 3, "agent": [SIM.RunResult("x", 0, {}, _groups(28, 12))] * 3}, (1, 2, 3)
    )
    rows = {(r["decision"], r["arm"]): r for r in FA.forward(cmp)}
    assert rows[("bed_alarm", "current rules")]["within_limit"] and not rows[("bed_alarm", "agent")]["within_limit"]
    assert not rows[("falls_per_1000_bed_days", "agent")]["within_limit"]  # 12 vs 10 per 1,000 is a gap of 2


# ------------------------------------------------------------------ CLI and gate
@pytest.mark.parametrize("step", [s for s in report.STEPS if s != "gate"])
def test_healthcare_cli_step_runs(step, capsys):
    assert main(["healthcare", step]) == 0
    assert capsys.readouterr().out.strip()


def test_healthcare_gate_passes():
    checks = report.gate_checks()
    assert len(checks) == 13 and all(ok for _n, ok, _d in checks), [c for c in checks if not c[1]]

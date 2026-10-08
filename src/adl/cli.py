"""adl: command line for the agentic data layer. Everything runs offline on synthetic data.

adl domains                 the four business domains and whether each is built or planned
adl contracts               validate every data contract (all domains) and summarise them
adl run                     build bronze -> silver -> gold and the insight products; quality per product
adl quality                 data-quality results per product (checks, completeness, quarantine)
adl lineage [--dataset D] [--out F]  OpenLineage events, the upstream of a dataset, optional JSONL file
adl metrics                 governed metrics from the semantic layer
adl value-case              Phase 0: KPI baselines and targets from config/value-case.yaml
adl forecast                demand forecast backtest against naive baselines
adl stockout                stockout-risk backtest against the days-of-cover rule
adl elasticity              price elasticity from the store price tests, against the simulator's truth
adl markdown                markdown candidates and recommended discounts
adl retrieval               knowledge retrieval evaluation (vector, graph, hybrid)
adl access                  access attack suite, PII leak scan and audit tamper check
adl agents                  run the replenishment and markdown workflow for every store (dry run)
adl injection               prompt-injection what-if: quoting and validator on and off
adl value                   value ledger with 95% intervals, KPI targets hit or missed, cost per outcome
adl focus [--out F]         FOCUS 1.0 cost rows for the AI and platform estimate
adl tune                    the policy settings grid on the tuning seeds
adl adapters                cloud storage adapters against fake clients
adl iac                     static summary of the Terraform, Bicep and workflows (no cloud, no terraform run)
adl mcp --identity I | mcp-demo
adl a2a-demo
adl mortgage STEP [--out F] the mortgage domain: run, quality, lineage, metrics, value-case, risk, retrieval,
                            access, agents, injection, value, focus, gate
adl gate                    release gate for every built domain (exit 1 on any failure)
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import sys
from pathlib import Path


def _quiet() -> None:
    logging.basicConfig(level=logging.WARNING)
    for name in ("agent_framework", "httpx"):
        logging.getLogger(name).setLevel(logging.ERROR)
    logging.getLogger("mcp").setLevel(logging.CRITICAL)  # expected denials are logged at ERROR; the client sees is_error


def _table(rows: list[list], header: list[str]) -> str:
    rows = [[str(c) for c in r] for r in rows]
    w = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    out = ["  ".join(h.ljust(w[i]) for i, h in enumerate(header)).rstrip(), "  ".join("-" * x for x in w)]
    out += ["  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in rows]
    return "\n".join(out)


def _usd(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _ci(lo: float, hi: float) -> str:
    return f"[{_usd(lo)}, {_usd(hi)}]"


def _lake():
    from adl.domains.retail import runtime

    return runtime.lake()


def _valued_lake():
    from adl.domains.retail import runtime

    return runtime.valued()[0]


# ------------------------------------------------------------------------------------------ data
def cmd_domains(a) -> int:
    from adl.core.domain import REGISTRY

    rows = []
    for d in REGISTRY.values():
        cs = d.contracts()
        rows.append([d.name, d.status, d.organisation, d.use_case, len(cs), ", ".join(d.levers)])
    print(_table(rows, ["domain", "status", "fictional organisation", "use case", "contracts", "levers"]))
    return 0


def cmd_contracts(a) -> int:
    from collections import Counter

    from adl.core.domain import REGISTRY

    rows = []
    for d in REGISTRY.values():
        cs = d.contracts()
        layers = Counter(c.layer for c in cs.values())
        rows.append(
            [
                d.name,
                d.status,
                len(cs),
                layers.get("silver", 0),
                layers.get("gold", 0),
                sum(c.agent_exposed for c in cs.values()),
                sum(bool(c.pii_columns) for c in cs.values()),
                sum(len(c.quality) for c in cs.values()),
            ]
        )
    print(_table(rows, ["domain", "status", "contracts", "silver", "gold", "agent-exposed", "with PII", "quality checks"]))
    print("\nall contracts valid: True")
    return 0


def cmd_run(a) -> int:
    lk = _valued_lake()
    b = lk.build
    print(f"bronze: {len(b.bronze_rows)} source tables, {sum(b.bronze_rows.values()):,} rows")
    for layer in ("silver", "gold"):
        qs = [q for k, q in sorted(b.quality.items()) if q.contract.layer == layer]
        print(f"{layer}: {len(qs)} products, {sum(q.rows for q in qs):,} rows, all passed: {all(q.passed for q in qs)}")
    dup = b.bronze_rows["pos_sales"] - b.quality["retail.silver.pos_sales"].rows - b.quarantined["retail.silver.pos_sales"]
    print(f"duplicate POS rows removed (a resent batch): {dup}")
    print(f"quarantined rows: {sum(b.quarantined.values())} ({', '.join(f'{k.split(".")[-1]} {v}' for k, v in sorted(b.quarantined.items()) if v)})")
    print(f"lineage events: {len(b.lineage.events)}")
    print(f"storage: {lk.store.name} ({lk.store.table_format}); auth: {lk.store.auth().method}")
    return 0


def cmd_quality(a) -> int:
    b = _valued_lake().build
    rows = []
    for k, q in sorted(b.quality.items()):
        failed = [r.check for r in q.results if not r.passed]
        rows.append(
            [
                k.split(".", 1)[1],
                q.rows,
                q.quarantined,
                f"{q.completeness_pct:.3f}%",
                sum(r.passed for r in q.results),
                len(q.results),
                "yes" if q.passed else "NO " + ",".join(failed),
            ]
        )
    print(_table(rows, ["product", "rows", "quarantined", "complete", "checks passed", "checks", "passed"]))
    return 0


def cmd_lineage(a) -> int:
    lin = _valued_lake().build.lineage
    from adl.core.lineage import validate_event

    problems = sum(len(validate_event(e)) for e in lin.events)
    print(f"events: {len(lin.events)}; schema problems: {problems}; edges: {len(lin.edges())}")
    known = {d for i, _, o in lin.edges() for d in (i, o)}
    if a.dataset not in known:
        print(f"unknown dataset {a.dataset}; known datasets: {len(known)}")
        return 1
    up = sorted(lin.upstream(a.dataset))
    print(f"upstream of {a.dataset} ({len(up)}):")
    for d in up:
        print(f"  {d}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        lin.write(Path(a.out))
        print(f"wrote {len(lin.events)} events to {a.out}")
    return 0


def cmd_metrics(a) -> int:
    lk = _lake()
    rows = lk.semantic.query(
        lk.store, ["revenue_usd", "stockout_rate_pct", "lost_sales_usd", "markdown_usd", "waste_cost_usd", "gross_margin_pct"], ["category"]
    )
    print("history under the current rules, days 0-139, by category (metrics layer):")
    print(
        _table(
            [
                [
                    r["category"],
                    _usd(r["revenue_usd"]),
                    f"{r['stockout_rate_pct']:.2f}%",
                    _usd(r["lost_sales_usd"]),
                    _usd(r["markdown_usd"]),
                    _usd(r["waste_cost_usd"]),
                    f"{r['gross_margin_pct']:.1f}%",
                ]
                for r in rows
            ],
            ["category", "revenue", "stockout rate", "est. lost sales", "markdown", "waste", "gross margin"],
        )
    )
    t = lk.semantic.query(lk.store, ["revenue_usd", "stockout_rate_pct", "lost_sales_usd", "markdown_usd", "waste_cost_usd"])[0]
    print(
        f"\ntotal: revenue {_usd(t['revenue_usd'])}, stockout rate {t['stockout_rate_pct']:.2f}%, est. lost sales {_usd(t['lost_sales_usd'])}, "
        f"markdown {_usd(t['markdown_usd'])}, waste {_usd(t['waste_cost_usd'])}"
    )
    from adl.domains.retail.synth import ground_truth

    gt = ground_truth(lk.history)
    true_lost = float((gt["lost"] * gt["price"]).sum())
    print(f"true lost sales in the simulator (evaluation only): {_usd(true_lost)}; the same-weekday estimate in gold.sales_daily is conservative")
    return 0


def cmd_value_case(a) -> int:
    from adl.domains.retail import value as V

    lk = _lake()
    case = V.load_case()
    print(f"use case: {case['use_case']} ({case['domain']})")
    print(f"problem: {case['business_problem']}")
    rows = [
        [k["metric"], f"{k['baseline']:,.2f}", f"{k['target_change_pct']:+d}%", f"{k['target']:,.2f}", k["why"]]
        for k in V.value_case(lk.store, lk.semantic)
    ]
    print(f"\nbaseline per {case['measurement_window_days']} days, measured from gold.sales_daily (history under the current rules):")
    print(_table(rows, ["kpi", "baseline", "target change", "target", "why"]))
    print("\nvalue tree (MIT CISR five steps):")
    for step, items in case["value_tree"].items():
        print(f"  {step}: {', '.join(items)}")
    return 0


# ------------------------------------------------------------------------------------------ insight
def cmd_forecast(a) -> int:
    from adl.domains.retail import runtime

    bt = runtime.forecast_backtest()
    print(f"rolling-origin backtest: 4 origins x 7 days, {bt.rows[0]['points']:,} store-product-days (sold-out days excluded)")
    print(_table([[r["model"], f"{r['wape_pct']:.1f}%", f"{r['bias_pct']:+.1f}%"] for r in bt.rows], ["model", "WAPE", "bias"]))
    print()
    cats = sorted({r["category"] for r in bt.by_category})
    get = {(r["model"], r["category"]): r["wape_pct"] for r in bt.by_category}
    print(
        _table(
            [[c, f"{get[('ridge model', c)]:.1f}%", f"{get[('same day last week', c)]:.1f}%", f"{get[('28-day mean', c)]:.1f}%"] for c in cats],
            ["category", "ridge", "last week", "28-day mean"],
        )
    )
    return 0


def cmd_stockout(a) -> int:
    from adl.domains.retail import runtime

    bt = runtime.risk_backtest()
    base = bt.positives / bt.samples
    print(f"backtest: 32 origins x 384 store-products = {bt.samples:,} cases; sold out within 3 days: {bt.positives:,} ({100 * base:.1f}%)")
    rows = [
        [
            r["method"],
            r["flagged"],
            f"{r['precision_pct']:.1f}%",
            f"{r['recall_pct']:.1f}%",
            f"{r['f1_pct']:.1f}%",
            "-" if r["auc"] != r["auc"] else f"{r['auc']:.3f}",
        ]
        for r in bt.rows
    ]
    print(_table(rows, ["method", "flagged", "precision", "recall", "F1", "AUC"]))
    print(
        f"Brier score: model {bt.rows[0]['brier']:.3f}; always predicting the base rate {base * (1 - base):.3f} (the probabilities rank well but are not calibrated)"
    )
    return 0


def cmd_elasticity(a) -> int:
    from adl.domains.retail.world import CATEGORIES

    lk = _lake()
    print(f"difference-in-differences on {len(lk.tests)} store price tests (test stores vs the other stores, 7 days vs the 14 before):")
    rows = [[c, f"{lk.elasticity[c]:.2f}", f"{CATEGORIES[c]['elasticity']:.2f}", f"{lk.take30[c]:.3f}"] for c in CATEGORIES]
    print(_table(rows, ["category", "estimated", "simulator truth", "near-date take rate at 30% off"]))
    return 0


def cmd_markdown(a) -> int:
    lk = _lake()
    rows = lk.store.sql(
        "SELECT category, COUNT(*) AS n, SUM(near_expiry_units) AS near, AVG(recommended_discount_pct) AS rec, SUM(expected_units_cleared) AS cleared FROM gold.markdown_candidates GROUP BY category ORDER BY category"
    )
    print("gold.markdown_candidates at the as-of day (current rule: 30% on everything near date):")
    print(
        _table(
            [[r["category"], r["n"], r["near"], f"{r['rec']:.1f}%", f"{r['cleared']:.1f}"] for r in rows],
            ["category", "candidates", "near-date units", "avg recommended", "expected cleared"],
        )
    )
    return 0


def cmd_retrieval(a) -> int:
    from adl.domains.retail import runtime
    from adl.domains.retail.knowledge import load_docs, load_questions

    res = runtime.retrieval_eval()
    g = _lake().index.graph.stats()
    print(f"corpus: {len(load_docs())} documents; questions: {len(load_questions())}; graph: {g}")
    print(
        _table(
            [[m, *(f"{v[k]:.3f}" for k in ("recall@3", "recall@5", "mrr@10", "ndcg@5", "hit@5"))] for m, v in res.items()],
            ["method", "recall@3", "recall@5", "MRR@10", "nDCG@5", "hit@5"],
        )
    )
    return 0


# ------------------------------------------------------------------------------------------ access and agents
def cmd_access(a) -> int:
    from adl.domains.retail import attacks, runtime

    gw = runtime.fresh_gateway()
    res = attacks.run(gw)
    print(
        _table([[r["attempt"], r["identity"], r["got"], "yes" if r["stopped"] else "NO"] for r in res], ["attempt", "identity", "denial", "stopped"])
    )
    leaks = attacks.pii_leaks(gw)
    print(f"\nstopped: {sum(r['stopped'] for r in res)}/{len(res)}")
    print(f"PII scan of {len(leaks)} agent-exposed products: {sum(leaks.values())} rows with an email, phone or member name")
    _ok, msg = gw.audit.verify()
    print(
        f"audit log: {msg}; denials recorded {len(gw.audit.events('data.denied')) + len(gw.audit.events('metric.denied')) + len(gw.audit.events('knowledge.denied'))}"
    )
    detected, why = attacks.tamper_detected(gw)
    print(f"tampered copy detected: {detected} ({why})")
    return 0


def cmd_agents(a) -> int:
    from adl.domains.retail import agents as A
    from adl.domains.retail import runtime

    gw, _plans, cases = runtime.agent_run()
    s = A.summarize(cases)
    print(f"workflow: plan -> narrate -> policy -> approval gate (when needed) -> execute (dry run); {s['stores']} stores")
    rows = []
    for c in cases:
        k = {x: sum(1 for y in c.actions if y.kind == x) for x in ("purchase_order", "transfer", "markdown")}
        rows.append(
            [
                c.store_id,
                k["purchase_order"],
                k["transfer"],
                k["markdown"],
                len(c.pending),
                len(c.decision.rejected) if c.decision else 0,
                len(c.executed),
                ",".join(c.brief.flagged_notes) or "-",
            ]
        )
    print(_table(rows, ["store", "POs", "transfers", "markdowns", "for approval", "rejected", "dry-run", "flagged notes"]))
    print(
        f"\nactions {s['actions']}: below threshold {s['auto_below_threshold']}, sent to a person {s['sent_for_approval']}, rejected {s['rejected_by_person']}, "
        f"executed as dry run {s['executed_dry_run']}; PO value at cost {_usd(s['po_value_usd'])}; fallback briefs {s['fallback_briefs']}"
    )
    c = cases[2]
    print(f"\nexample brief ({c.store_id}): {c.brief.headline}. {c.brief.summary}")
    _ok, msg = gw.audit.verify()
    print(f"audit: {msg}")
    return 0


def cmd_injection(a) -> int:
    from adl.domains.retail import runtime

    rows = runtime.injection_eval()
    print("gullible model; stores S03, S05 and S07 each have one store note carrying an injected instruction")
    print(
        _table(
            [
                [
                    r["quoting"],
                    r["validator"],
                    f"{r['model_obeyed']}/3",
                    f"{r['shown_to_approver']}/3",
                    r["fallback_briefs"],
                    r["injected_actions_executed"],
                ]
                for r in rows
            ],
            ["quoting", "validator", "model obeyed", "shown to approver", "fallback briefs", "injected actions executed"],
        )
    )
    print("\nactions always come from code, so an obeyed injection can mislead the brief but never reach execution")
    return 0


# ------------------------------------------------------------------------------------------ value
def _cost(cases, stores: int):
    from adl.domains.retail import value as V

    prompt = round(sum(c.prompt_tokens for c in cases) / len(cases))
    out = round(sum(c.output_tokens for c in cases) / len(cases))
    return V.ai_cost(prompt, out, stores)


def cmd_value(a) -> int:
    from adl.domains.retail import runtime
    from adl.domains.retail import value as V

    _lk, led = runtime.valued()
    cmp = led.comparison
    print(f"forward simulation: {len(cmp.seeds)} replications x 28 days, common random numbers, paired 95% bootstrap intervals")
    rows = [
        [
            r["ledger_id"],
            r["lever"],
            r["metric"].replace("_usd", ""),
            _usd(r["baseline_usd"]),
            _usd(r["agent_usd"]),
            _usd(r["delta_usd"]),
            _ci(r["ci_low_usd"], r["ci_high_usd"]),
        ]
        for r in led.rows
    ]
    print(_table(rows, ["ledger", "lever", "metric", "current rules", "with agents", "change", "95% interval"]))
    print("\nKPI targets (config/value-case.yaml), all levers vs the current rules:")
    krows = [
        [
            k["metric"],
            f"{k['current_rules']:,.2f}",
            f"{k['agent']:,.2f}",
            f"{k['change_pct']:+.1f}%",
            f"{k['target_change_pct']:+d}%",
            "met" if k["met"] else "MISSED",
        ]
        for k in V.kpi_results(cmp)
    ]
    print(_table(krows, ["kpi", "current rules", "with agents", "change", "target", "result"]))
    _gw, _plans, cases = runtime.agent_run()
    cost = _cost(cases, len(cases))
    net = led.row("all levers", "net_value_usd")
    acts = cmp.actions("agent", "purchase_order_lines") + cmp.actions("agent", "markdowns") + cmp.actions("agent", "transfers")
    print(f"\nestimated AI and platform cost for 28 days (assumed rates in config/pricing.yaml): {_usd(cost.total_usd)}")
    for line in cost.lines:
        print(f"  {line['service']}: ${line['cost_usd']:,.2f} ({line['quantity']:,.0f} {line['unit']})")
    print(f"  prompt {cost.prompt_tokens} tokens and output {cost.output_tokens} tokens per brief (measured), {cost.briefs} briefs")
    print(
        f"cost per $1,000 of net value: ${cost.per_1000_value(net['delta_usd']):,.2f}; per action: ${cost.total_usd / acts:,.4f} ({acts:,.0f} actions)"
    )
    print(f"value after cost: {_usd(net['delta_usd'] - cost.total_usd)} per 28 days ({len(cmp.seeds)}-replication mean)")
    return 0


def cmd_focus(a) -> int:
    from adl.domains.retail import runtime
    from adl.domains.retail import value as V

    _gw, _plans, cases = runtime.agent_run()
    cost = _cost(cases, len(cases))
    rows = V.focus_rows(cost, "VL-RET-001")
    text = V.focus_csv(rows)
    if a.out:
        Path(a.out).write_text(text)
    print(f"FOCUS 1.0 rows: {len(rows)} ({len(V.FOCUS_COLUMNS)} columns), services: {', '.join(sorted({r['ServiceName'] for r in rows}))}")
    print(f"total EffectiveCost: ${sum(r['EffectiveCost'] for r in rows):,.2f}; tags: {rows[0]['Tags']}")
    print("billing period is a synthetic placeholder; load the CSV with the Jagadish-azure-finops tooling to put cost next to value")
    return 0


def cmd_tune(a) -> int:
    from adl.domains.retail import policy as PO
    from adl.domains.retail import simulate as SIM
    from adl.domains.retail import world as W

    lk = _lake()
    h = lk.history
    pols = {"current rules": W.LegacyPolicy()}
    grid = [(z, md) for z in (-0.25, 0.0, 0.5) for md in (0.2, 0.3, 0.5)]
    for z, md in grid:
        cfg = copy.deepcopy(lk.policy)
        cfg["replenishment"]["service_z"]["perishable"] = z
        cfg["markdown"]["max_discount"] = md
        pols[f"z={z:+.2f} max={md:.1f}"] = PO.AgentPolicy(h.world, lk.frame, lk.model, lk.elasticity, lk.take30, cfg)
    cmp = SIM.compare(h.world, h.calendar, h.state, pols, SIM.TUNE_SEEDS)
    print(f"tuning seeds {SIM.TUNE_SEEDS[0]}-{SIM.TUNE_SEEDS[-1]} (never the reported seeds); change vs the current rules over 28 days")
    rows = []
    for name in list(pols)[1:]:
        rows.append(
            [
                name,
                _usd(cmp.diff(name, "net_value_usd")[0]),
                _usd(cmp.diff(name, "lost_sales_usd")[0]),
                _usd(cmp.diff(name, "waste_cost_usd")[0]),
                _usd(cmp.diff(name, "markdown_usd")[0]),
            ]
        )
    print(_table(rows, ["perishable z, max markdown", "net value", "lost sales", "waste", "markdown"]))
    print(
        f"\nchosen (config/policy.yaml): z={lk.policy['replenishment']['service_z']['perishable']:+.2f} max={lk.policy['markdown']['max_discount']:.1f}; see docs/adr/0004-policy-tuning.md"
    )
    return 0


def cmd_adapters(a) -> int:
    from adl.storage import fake_adapters

    lk = _lake()
    t = lk.store.read("gold", "stockout_risk")
    rows = []
    for name, st in fake_adapters().items():
        st.write("gold", "stockout_risk", t)
        got = st.sql(f"SELECT COUNT(*) AS n FROM {st.qualified('gold', 'stockout_risk')} WHERE risk_band = ?", ["high"])[0]["n"]
        rows.append([name, st.uri("gold", "stockout_risk"), st.qualified("gold", "stockout_risk"), st.auth().method, got])
    local = lk.store.sql("SELECT COUNT(*) AS n FROM gold.stockout_risk WHERE risk_band = ?", ["high"])[0]["n"]
    print(_table(rows, ["adapter", "uri", "table", "auth", "high-risk rows"]))
    print(f"\nlocal adapter high-risk rows: {local}; every adapter secretless: {all(s.auth().secretless for s in fake_adapters().values())}")
    print("cloud adapters run against fake clients only; none has been run against a real account")
    return 0


def cmd_iac(a) -> int:
    from adl import iac

    tf = iac.terraform()
    print(
        _table(
            [[s["stack"], s["resources"], s["data"], s["variables"], s["outputs"], s["test_runs"]] for s in tf],
            ["terraform stack", "resources", "data sources", "variables", "outputs", "test runs"],
        )
    )
    print()
    rows = [[s["stack"], name, "yes" if ok else "NO"] for s in tf for name, ok in s["controls"].items()]
    b = iac.bicep()
    rows += [["bicep", name, "yes" if ok else "NO"] for name, ok in b["controls"].items()]
    print(_table(rows, ["stack", "control", "set"]))
    print(f"\nbicep: {b['files']} files, {b['resources']} resource declarations, {b['modules']} modules")
    print(f"checkov skips with a written reason: {iac.checkov_skips()}")
    print()
    wf = iac.workflows()
    print(
        _table(
            [
                [
                    w["workflow"],
                    ", ".join(map(str, w["triggers"])),
                    "yes" if w["read_only"] else "NO",
                    f"{w['pinned']}/{w['actions']}",
                    w["jobs"],
                    w["gated"],
                    w["oidc"],
                ]
                for w in wf
            ],
            ["workflow", "triggers", "read-only default", "SHA-pinned actions", "jobs", "gated by DEPLOY_ENABLED", "OIDC jobs"],
        )
    )
    print("\nstatic read of the files only; nothing has been applied to any cloud")
    return 0 if iac.controls_ok() else 1


def cmd_mcp(a) -> int:  # pragma: no cover - long-running stdio server
    from adl.domains.retail import runtime
    from adl.serve.mcp_server import build_server

    build_server(runtime.fresh_gateway(), a.identity).run()
    return 0


def cmd_mcp_demo(a) -> int:
    from adl.domains.retail import runtime
    from adl.serve.mcp_server import demo

    for line in asyncio.run(demo(runtime.fresh_gateway())):
        print(line)
    return 0


def cmd_a2a_demo(a) -> int:
    from adl.domains.retail import runtime
    from adl.serve.a2a import demo

    for line in demo(runtime.fresh_gateway()):
        print(line)
    return 0


# ------------------------------------------------------------------------------------------ gate
def gate_checks() -> list[tuple[str, bool, str]]:
    from adl.core.domain import REGISTRY
    from adl.core.lineage import validate_event
    from adl.domains.retail import attacks, runtime
    from adl.domains.retail import value as V
    from adl.storage import fake_adapters

    lk, led = runtime.valued()
    out = []
    n = sum(len(d.contracts()) for d in REGISTRY.values())
    out.append(("contracts valid (all domains)", True, f"{n} contracts"))
    q = lk.build.quality
    out.append(("every built product passes its contract", all(x.passed for x in q.values()), f"{sum(x.passed for x in q.values())}/{len(q)}"))
    probs = sum(len(validate_event(e)) for e in lk.build.lineage.events)
    out.append(("lineage events valid", probs == 0, f"{len(lk.build.lineage.events)} events"))
    fb = {r["model"]: r["wape_pct"] for r in runtime.forecast_backtest().rows}
    out.append(
        (
            "forecast beats both naive baselines",
            fb["ridge model"] < min(fb["same day last week"], fb["28-day mean"]),
            f"WAPE {fb['ridge model']:.1f}%",
        )
    )
    rb = runtime.risk_backtest().rows
    out.append(
        (
            "stockout model beats the cover rule on F1",
            rb[0]["f1_pct"] > rb[1]["f1_pct"] and rb[0]["auc"] > 0.7,
            f"F1 {rb[0]['f1_pct']:.1f}% vs {rb[1]['f1_pct']:.1f}%",
        )
    )
    rr = runtime.retrieval_eval()["hybrid"]["recall@5"]
    out.append(("hybrid retrieval recall@5 >= 0.90", rr >= 0.9, f"{rr:.3f}"))
    gw = runtime.fresh_gateway()
    att = attacks.run(gw)
    out.append(("every access attack stopped", all(r["stopped"] for r in att), f"{sum(r['stopped'] for r in att)}/{len(att)}"))
    leaks = sum(attacks.pii_leaks(gw).values())
    out.append(("no PII in agent-exposed products", leaks == 0, f"{leaks} rows"))
    out.append(("audit chain verifies and detects tampering", gw.audit.verify()[0] and attacks.tamper_detected(gw)[0], gw.audit.verify()[1]))
    inj = runtime.injection_eval()
    out.append(("no injected action ever executed", all(r["injected_actions_executed"] == 0 for r in inj), "4 configurations"))
    out.append(
        (
            "with both defences nothing injected reaches the approver",
            inj[0]["shown_to_approver"] == 0 and inj[0]["model_obeyed"] == 0,
            "quoting on, validator on",
        )
    )
    _gw, _plans, cases = runtime.agent_run()
    over = [x for c in cases for x in c.executed if x.needs_approval and (not c.decision or x.id not in c.decision.approved)]
    out.append(
        (
            "nothing above a threshold executed without a person",
            not over and all(c.decision.approver.startswith("person:") for c in cases if c.decision),
            f"{len(over)} violations",
        )
    )
    net = led.row("all levers", "net_value_usd")
    out.append(("net value interval above zero", net["ci_low_usd"] > 0, _ci(net["ci_low_usd"], net["ci_high_usd"])))
    met = V.kpi_results(led.comparison)
    out.append(("KPI targets reported (hit or miss)", len(met) == len(V.load_case()["kpis"]), f"{sum(k['met'] for k in met)}/{len(met)} met"))
    ads = fake_adapters()
    ok = all(s.auth().secretless for s in ads.values())
    out.append(("cloud adapters secretless", ok, f"{len(ads)} adapters"))
    return out


def cmd_mortgage(a) -> int:
    from adl.domains.mortgage import report

    return report.main(a.step, a.out)


def all_gate_checks() -> list[tuple[str, bool, str]]:
    from adl.domains.mortgage import report

    return [(f"retail: {n}", ok, d) for n, ok, d in gate_checks()] + [(f"mortgage: {n}", ok, d) for n, ok, d in report.gate_checks()]


def cmd_gate(a) -> int:
    checks = all_gate_checks()
    print(_table([[name, "pass" if ok else "FAIL", detail] for name, ok, detail in checks], ["check", "result", "detail"]))
    failed = [c for c in checks if not c[1]]
    print(f"\nrelease gate: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    _quiet()
    ap = argparse.ArgumentParser(prog="adl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    simple = {
        "domains": cmd_domains,
        "contracts": cmd_contracts,
        "run": cmd_run,
        "quality": cmd_quality,
        "metrics": cmd_metrics,
        "value-case": cmd_value_case,
        "forecast": cmd_forecast,
        "stockout": cmd_stockout,
        "elasticity": cmd_elasticity,
        "markdown": cmd_markdown,
        "retrieval": cmd_retrieval,
        "access": cmd_access,
        "agents": cmd_agents,
        "injection": cmd_injection,
        "value": cmd_value,
        "tune": cmd_tune,
        "adapters": cmd_adapters,
        "iac": cmd_iac,
        "mcp-demo": cmd_mcp_demo,
        "a2a-demo": cmd_a2a_demo,
        "gate": cmd_gate,
    }
    for name, fn in simple.items():
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("lineage")
    p.add_argument("--dataset", default="gold.value_ledger")
    p.add_argument("--out", help="write the events as JSON lines, e.g. out/lineage/events.jsonl")
    p.set_defaults(fn=cmd_lineage)
    p = sub.add_parser("focus")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_focus)
    p = sub.add_parser("mortgage")
    from adl.domains.mortgage.report import STEPS

    p.add_argument("step", choices=STEPS)
    p.add_argument("--out", help="for focus: write the FOCUS CSV here")
    p.set_defaults(fn=cmd_mortgage)
    p = sub.add_parser("mcp")
    p.add_argument("--identity", default="agent:store-copilot-north")
    p.set_defaults(fn=cmd_mcp)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

"""`adl mortgage <step>`: the mortgage domain's reports and its release-gate checks.

Every number in docs/components/mortgage-*.md is printed by one of these steps and rendered into the
docs by scripts/render_docs.py, so the docs cannot drift from the code.
"""

from __future__ import annotations

from pathlib import Path

STEPS = ("run", "quality", "lineage", "metrics", "value-case", "risk", "retrieval", "access", "agents", "injection", "value", "focus", "gate")


def _fmt():
    from adl.cli import _ci, _table, _usd

    return _table, _usd, _ci


def _cost(cases):
    from adl.domains.mortgage import value as V

    prompt = round(sum(c.prompt_tokens for c in cases) / len(cases))
    out = round(sum(c.output_tokens for c in cases) / len(cases))
    return V.cost(prompt, out)


def run() -> int:
    from adl.domains.mortgage import runtime

    lk = runtime.valued()[0]
    b = lk.build
    print(f"bronze: {len(b.bronze_rows)} source tables, {sum(b.bronze_rows.values()):,} rows")
    for layer in ("silver", "gold"):
        qs = [q for _k, q in sorted(b.quality.items()) if q.contract.layer == layer]
        print(f"{layer}: {len(qs)} products, {sum(q.rows for q in qs):,} rows, all passed: {all(q.passed for q in qs)}")
    dup = b.bronze_rows["stage_events"] - b.quality["mortgage.silver.stage_events"].rows - b.quarantined["mortgage.silver.stage_events"]
    print(f"duplicate stage events removed (a resent batch): {dup}")
    print(f"quarantined rows: {sum(b.quarantined.values())} ({', '.join(f'{k.split(".")[-1]} {v}' for k, v in sorted(b.quarantined.items()) if v)})")
    print(f"lineage events: {len(b.lineage.events)}")
    print(f"active locks on the as-of morning: {lk.store.sql('SELECT count(*) AS n FROM gold.lock_position')[0]['n']}")
    return 0


def quality() -> int:
    from adl.domains.mortgage import runtime

    table, _, _ = _fmt()
    b = runtime.valued()[0].build
    rows = []
    for k, q in sorted(b.quality.items()):
        failed = [r.check for r in q.results if not r.passed]
        rows.append(
            [
                k.removeprefix("mortgage."),
                q.rows,
                q.quarantined,
                f"{q.completeness_pct:.2f}%",
                len(q.results),
                ", ".join(failed) or "-",
                "pass" if q.passed else "FAIL",
            ]
        )
    print(table(rows, ["product", "rows", "quarantined", "completeness", "checks", "failed", "result"]))
    return 0


def lineage() -> int:
    from adl.core.lineage import validate_event
    from adl.domains.mortgage import runtime

    lin = runtime.valued()[0].build.lineage
    probs = sum(len(validate_event(e)) for e in lin.events)
    print(f"OpenLineage events: {len(lin.events)} ({len(lin.events) // 2} runs), validation problems: {probs}")
    up = sorted(lin.upstream("gold.fallout_risk"))
    print(f"upstream of gold.fallout_risk ({len(up)}): {', '.join(up)}")
    return 0


def metrics() -> int:
    from adl.domains.mortgage import runtime

    table, _, _ = _fmt()
    lk = runtime.lake()
    rows = lk.semantic.query(
        lk.store, ["new_locks", "closed_loans", "pull_through_pct", "withdrawal_rate_pct", "extension_cost_usd", "cycle_days"], ["region"]
    )
    print(
        table(
            [
                [
                    r["region"],
                    r["new_locks"],
                    r["closed_loans"],
                    f"{r['pull_through_pct']:.1f}%",
                    f"{r['withdrawal_rate_pct']:.1f}%",
                    f"${r['extension_cost_usd']:,.0f}",
                    f"{r['cycle_days']:.1f}",
                ]
                for r in rows
            ],
            ["region", "locks", "closed", "pull-through", "withdrawals", "extension cost", "lock-to-close days"],
        )
    )
    sql, params = lk.semantic.compile(["pull_through_pct"], ["channel"], [("region", "eq", "north")])
    print(f"\ncompiled: {sql} -- params {params}")
    return 0


def value_case() -> int:
    from adl.domains.mortgage import runtime
    from adl.domains.mortgage import value as V

    table, _, _ = _fmt()
    lk = runtime.lake()
    case = V.load_case()
    print(f"use case: {case['use_case']}; window {case['measurement_window_days']} days; baseline measured over the 180-day history")
    rows = [
        [k["metric"], f"{k['baseline']:,.2f}", f"{k['target_change_pct']:+d}%", f"{k['target']:,.2f}", k["why"]]
        for k in V.value_case(lk.store, lk.semantic)
    ]
    print(table(rows, ["kpi", "baseline", "target change", "target", "why"]))
    return 0


def risk() -> int:
    from adl.domains.mortgage import runtime
    from adl.domains.mortgage import world as W

    table, _, _ = _fmt()
    bt = runtime.risk_backtest()
    print(f"backtest: {bt.origins} daily origins, {bt.locks_scored:,} active locks scored, 14-day fallout rate {bt.fallout_rate_pct:.1f}%")
    print(f"each method picks the {W.CALL_CAPACITY} locks the loan officers can call per day")
    print(
        table(
            [
                [
                    r["method"],
                    f"{r['auc']:.3f}",
                    f"{r['precision_pct']:.1f}%",
                    f"{r['recall_pct']:.1f}%",
                    f"{r['value_at_risk_covered_pct']:.1f}%",
                    f"{r['brier']:.4f}",
                ]
                for r in bt.rows
            ],
            ["method", "AUC", "precision@60", "recall@60", "value at risk covered", "Brier"],
        )
    )
    print("Brier for the rule is the base-rate forecast (the rule gives no probability)")
    lk = runtime.lake()
    print(
        f"\ngold.fallout_risk: {', '.join(f'{r["risk_band"]} {r["n"]}' for r in lk.store.sql('SELECT risk_band, count(*) AS n FROM gold.fallout_risk GROUP BY 1 ORDER BY 2'))}"
    )
    return 0


def retrieval() -> int:
    from adl.domains.mortgage import runtime
    from adl.domains.mortgage.knowledge import load_questions
    from adl.knowledge.retrieve import METHODS

    table, _, _ = _fmt()
    ev = runtime.retrieval_eval()
    lk = runtime.lake()
    print(f"corpus: {len(lk.index.docs)} documents; questions: {len(load_questions())}; graph: {lk.index.graph.stats()}")
    print(
        table(
            [
                [
                    m,
                    f"{ev[m]['recall@3']:.3f}",
                    f"{ev[m]['recall@5']:.3f}",
                    f"{ev[m]['mrr@10']:.3f}",
                    f"{ev[m]['ndcg@5']:.3f}",
                    f"{ev[m]['hit@5']:.3f}",
                ]
                for m in METHODS
            ],
            ["method", "recall@3", "recall@5", "MRR@10", "nDCG@5", "hit@5"],
        )
    )
    return 0


def access() -> int:
    from adl.domains.mortgage import attacks, runtime

    table, _, _ = _fmt()
    gw = runtime.fresh_gateway()
    res = attacks.run(gw)
    print(
        table([[r["attempt"], r["identity"], r["got"], "yes" if r["stopped"] else "NO"] for r in res], ["attempt", "identity", "denial", "stopped"])
    )
    leaks = attacks.pii_leaks(gw)
    print(f"\nstopped: {sum(r['stopped'] for r in res)}/{len(res)}")
    print(f"PII scan of {len(leaks)} agent-exposed products: {sum(leaks.values())} rows with an e-mail, phone or applicant name")
    _ok, msg = gw.audit.verify()
    print(f"audit log: {msg}; denials recorded {sum(len(gw.audit.events(e)) for e in ('data.denied', 'metric.denied', 'knowledge.denied'))}")
    detected, why = attacks.tamper_detected(gw)
    print(f"tampered copy detected: {detected} ({why})")
    return 0


def agents() -> int:
    from adl.core import agentflow
    from adl.domains.mortgage import agents as A
    from adl.domains.mortgage import runtime

    table, _, _ = _fmt()
    gw, _plans, cases = runtime.agent_run()
    s = agentflow.summarize(cases, A.KINDS)
    print(f"workflow: plan -> narrate -> policy -> approval gate (when needed) -> execute (dry run); {s['groups']} loan officers")
    rows = []
    for c in cases:
        k = {x: sum(1 for y in c.actions if y.kind == x) for x in A.KINDS}
        rows.append(
            [
                c.group,
                k["outreach_call"],
                k["document_chase"],
                k["lock_extension"],
                len(c.pending),
                len(c.decision.rejected) if c.decision else 0,
                len(c.executed),
                ",".join(c.brief.flagged_notes) or "-",
            ]
        )
    print(table(rows, ["officer", "calls", "chases", "extensions", "for approval", "rejected", "dry-run", "flagged notes"]))
    print(
        f"\nactions {s['actions']}: below threshold {s['auto_below_threshold']}, sent to a person {s['sent_for_approval']}, "
        f"rejected {s['rejected_by_person']}, executed as dry run {s['executed_dry_run']}; fallback briefs {s['fallback_briefs']}"
    )
    c = cases[0]
    print(f"\nexample brief ({c.group}): {c.brief.headline}. {c.brief.summary}")
    print(f"audit: {gw.audit.verify()[1]}")
    return 0


def injection() -> int:
    from adl.domains.mortgage import runtime

    table, _, _ = _fmt()
    rows = runtime.injection_eval()
    n = len(runtime.INJECTED_GROUPS)
    print(f"gullible model; loan officers {', '.join(runtime.INJECTED_GROUPS)} each have one pipeline note carrying an injected instruction")
    print(
        table(
            [
                [
                    r["quoting"],
                    r["validator"],
                    f"{r['model_obeyed']}/{n}",
                    f"{r['shown_to_approver']}/{n}",
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


def value() -> int:
    from adl.domains.mortgage import runtime
    from adl.domains.mortgage import value as V

    table, usd, ci = _fmt()
    _lk, led = runtime.valued()
    cmp = led.comparison
    print(f"forward simulation: {len(cmp.seeds)} replications x 28 days, common random numbers, paired 95% bootstrap intervals")
    rows = [
        [
            r["ledger_id"],
            r["lever"],
            r["metric"].replace("_usd", ""),
            usd(r["baseline_usd"]),
            usd(r["agent_usd"]),
            usd(r["delta_usd"]),
            ci(r["ci_low_usd"], r["ci_high_usd"]),
        ]
        for r in led.rows
    ]
    print(table(rows, ["ledger", "lever", "metric", "current rules", "with agents", "change", "95% interval"]))
    print("\nKPI targets (config/mortgage/value-case.yaml), all levers vs the current rules:")
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
    print(table(krows, ["kpi", "current rules", "with agents", "change", "target", "result"]))
    _gw, _plans, cases = runtime.agent_run()
    cost = _cost(cases)
    net = led.row("all levers", "net_value_usd")
    acts = cmp.actions("agent", "calls") + cmp.actions("agent", "chases") + cmp.actions("agent", "extensions")
    extra_closed = (cmp.mean("agent", "pull_through_pct") - cmp.mean("current rules", "pull_through_pct")) / 100
    print(f"\nestimated AI and platform cost for 28 days (assumed rates in config/pricing.yaml): {usd(cost.total_usd)}")
    for line in cost.lines:
        print(f"  {line['service']}: ${line['cost_usd']:,.2f} ({line['quantity']:,.0f} {line['unit']})")
    print(f"  prompt {cost.prompt_tokens} tokens and output {cost.output_tokens} tokens per brief (measured), {cost.briefs} briefs")
    print(
        f"cost per $1,000 of net value: ${cost.per_1000_value(net['delta_usd']):,.2f}; per action: ${cost.total_usd / acts:,.4f} ({acts:,.0f} actions)"
    )
    print(
        f"pull-through change: {100 * extra_closed:+.2f} points; value after cost: {usd(net['delta_usd'] - cost.total_usd)} per 28 days ({len(cmp.seeds)}-replication mean)"
    )
    return 0


def focus(out: str | None = None) -> int:
    from adl.core import finops
    from adl.domains.mortgage import runtime
    from adl.domains.mortgage import value as V

    _gw, _plans, cases = runtime.agent_run()
    rows = V.focus(_cost(cases))
    if out:
        Path(out).write_text(finops.focus_csv(rows))
    print(f"FOCUS 1.0 rows: {len(rows)} ({len(finops.FOCUS_COLUMNS)} columns), services: {', '.join(sorted({r['ServiceName'] for r in rows}))}")
    print(f"total EffectiveCost: ${sum(r['EffectiveCost'] for r in rows):,.2f}; tags: {rows[0]['Tags']}")
    return 0


def gate_checks() -> list[tuple[str, bool, str]]:
    from adl.core.lineage import validate_event
    from adl.domains.mortgage import attacks, runtime
    from adl.domains.mortgage import value as V

    _, _, ci = _fmt()
    lk, led = runtime.valued()
    out = []
    q = lk.build.quality
    out.append(("every built product passes its contract", all(x.passed for x in q.values()), f"{sum(x.passed for x in q.values())}/{len(q)}"))
    probs = sum(len(validate_event(e)) for e in lk.build.lineage.events)
    out.append(("lineage events valid", probs == 0, f"{len(lk.build.lineage.events)} events"))
    m, b = runtime.risk_backtest().rows
    out.append(
        (
            "fallout model beats the expiry rule (AUC and precision@60)",
            m["auc"] > b["auc"] and m["precision_pct"] > b["precision_pct"],
            f"AUC {m['auc']:.3f} vs {b['auc']:.3f}",
        )
    )
    rr = runtime.retrieval_eval()["hybrid"]["recall@5"]
    out.append(("hybrid retrieval recall@5 >= 0.90", rr >= 0.9, f"{rr:.3f}"))
    gw = runtime.fresh_gateway()
    att = attacks.run(gw)
    out.append(("every access attack stopped", all(r["stopped"] for r in att), f"{sum(r['stopped'] for r in att)}/{len(att)}"))
    leaks = sum(attacks.pii_leaks(gw).values())
    out.append(("no borrower PII in agent-exposed products", leaks == 0, f"{leaks} rows"))
    out.append(("audit chain verifies and detects tampering", gw.audit.verify()[0] and attacks.tamper_detected(gw)[0], gw.audit.verify()[1]))
    inj = runtime.injection_eval()
    out.append(("no injected action ever executed", all(r["injected_actions_executed"] == 0 for r in inj), "4 configurations"))
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
    out.append(("net value interval above zero", net["ci_low_usd"] > 0, ci(net["ci_low_usd"], net["ci_high_usd"])))
    met = V.kpi_results(led.comparison)
    out.append(("KPI targets reported (hit or miss)", len(met) == len(V.load_case()["kpis"]), f"{sum(k['met'] for k in met)}/{len(met)} met"))
    return out


def gate() -> int:
    table, _, _ = _fmt()
    checks = gate_checks()
    print(table([[n, "pass" if ok else "FAIL", d] for n, ok, d in checks], ["check", "result", "detail"]))
    failed = [c for c in checks if not c[1]]
    print(f"\nmortgage gate: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return 1 if failed else 0


def main(step: str, out: str | None = None) -> int:
    if step == "focus":
        return focus(out)
    return globals()[step.replace("-", "_")]()

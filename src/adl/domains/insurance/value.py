"""Value for the insurance domain: the Phase 0 value case, the value ledger, KPI results, cost per
outcome and FOCUS cost rows.

* `value_case` measures the KPI baselines with the metrics layer: cycle days, reopen rate and the
  audit-scaled leakage estimate from gold.claims_daily (leakage scaled to the 28-day window), backlog
  spread from gold.workload_daily. Each target comes from config/insurance/value-case.yaml.
* `ledger` turns the forward simulation into value-ledger rows: one per lever and metric, with the
  current-rules result, the agent result, the paired difference and its 95% bootstrap interval.
* `cost` estimates the AI and platform cost (adl.core.finops, assumed rates) for one brief per claims
  office per day; `focus` writes it as FOCUS 1.0 rows tagged with the ledger id.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from adl import ROOT
from adl.core import finops
from adl.core.semantic import SemanticLayer
from adl.domains.insurance import simulate as SIM
from adl.domains.insurance.world import DAYS_HISTORY, HORIZON, OFFICES

LEVERS = {"agent": "all levers", "triage only": "queue assignment", "review only": "leakage review", "referral only": "subrogation referral"}
LEDGER_METRICS = ("net_value_usd", "overpayment_paid_usd", "recoveries_usd", "leakage_usd")
WINDOW_SCALED = ("leakage_usd",)


def load_case() -> dict:
    return yaml.safe_load((ROOT / "config/insurance/value-case.yaml").read_text())


def value_case(store, semantic: SemanticLayer) -> list[dict]:
    case = load_case()
    window = case["measurement_window_days"]
    from_metrics = [k["metric"] for k in case["kpis"] if k["metric"] != "backlog_spread_days"]
    base = semantic.query(store, from_metrics)[0]
    base["backlog_spread_days"] = store.sql("SELECT avg(spread_days) AS s FROM gold.workload_daily")[0]["s"]
    out = []
    for k in case["kpis"]:
        m = k["metric"]
        b = base[m] * window / DAYS_HISTORY if m in WINDOW_SCALED else base[m]
        out.append(
            {
                "metric": m,
                "baseline": b,
                "target_change_pct": k["target_change_pct"],
                "target": b * (1 + k["target_change_pct"] / 100),
                "why": k["why"],
            }
        )
    return out


@dataclass
class Ledger:
    rows: list[dict]
    comparison: SIM.Comparison

    def row(self, lever: str, metric: str) -> dict:
        return next(r for r in self.rows if r["lever"] == lever and r["metric"] == metric)


def ledger(cmp: SIM.Comparison) -> Ledger:
    rows = []
    n = 0
    for arm, lever in LEVERS.items():
        for m in LEDGER_METRICS:
            n += 1
            d, lo, hi = cmp.diff(arm, m)
            rows.append(
                {
                    "ledger_id": f"VL-INS-{n:03d}",
                    "lever": lever,
                    "metric": m,
                    "baseline_usd": round(cmp.mean("current rules", m), 2),
                    "agent_usd": round(cmp.mean(arm, m), 2),
                    "delta_usd": round(d, 2),
                    "ci_low_usd": round(lo, 2),
                    "ci_high_usd": round(hi, 2),
                    "replications": len(cmp.seeds),
                    "horizon_days": HORIZON,
                }
            )
    return Ledger(rows, cmp)


def kpi_results(cmp: SIM.Comparison) -> list[dict]:
    out = []
    for k in load_case()["kpis"]:
        m = k["metric"]
        base = cmp.mean("current rules", m)
        d, lo, hi = cmp.diff("agent", m)
        change = 100 * d / base if base else 0.0
        met = change <= k["target_change_pct"] if k["direction"] == "down" else change >= k["target_change_pct"]
        out.append(
            {
                "metric": m,
                "current_rules": base,
                "agent": base + d,
                "change_pct": change,
                "target_change_pct": k["target_change_pct"],
                "met": met,
                "ci": (lo, hi),
            }
        )
    return out


def cost(prompt_tokens: int, output_tokens: int) -> finops.CostEstimate:
    return finops.ai_cost(prompt_tokens, output_tokens, len(OFFICES), HORIZON)


def focus(est: finops.CostEstimate, ledger_id: str = "VL-INS-001") -> list[dict]:
    tags = {"use-case": "insurance-claims-triage-leakage", "domain": "insurance", "value-ledger": ledger_id, "env": "simulation"}
    return finops.focus_rows(est, HORIZON, tags, "ba-ferrowind-insurance", "fw-data-products")

"""Value for the healthcare domain: the Phase 0 value case, the value ledger, KPI results, cost per
outcome and FOCUS cost rows. All figures come from synthetic data and stated cost assumptions.

* `value_case` measures the KPI baselines with the metrics layer from gold.ward_daily: falls per 1,000
  bed-days and time to intervention over the history, falls with harm and sitter shifts scaled to the
  28-day window. Each target comes from config/healthcare/value-case.yaml, committed before any build.
* `ledger` turns the forward simulation into value-ledger rows: one per lever and metric, with the
  current-rules result, the agent result, the paired difference and its 95% bootstrap interval.
* `cost` estimates the AI and platform cost (adl.core.finops, assumed rates) for one brief per ward per
  day; `focus` writes it as FOCUS 1.0 rows tagged with the ledger id.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from adl import ROOT
from adl.core import finops
from adl.core.semantic import SemanticLayer
from adl.domains.healthcare import simulate as SIM
from adl.domains.healthcare.world import DAYS_HISTORY, HORIZON, WARDS

LEVERS = {
    "agent": "all measures",
    "bed alarm only": "bed alarm",
    "rounding only": "hourly rounding",
    "mobility aid only": "mobility aid",
    "sitter only": "sitter request",
    "agent, sitters as today": "all measures except sitters",
}
LEDGER_METRICS = ("net_value_usd", "fall_cost_usd", "harm_fall_cost_usd", "measure_cost_usd")
WINDOW_SCALED = ("harm_falls", "sitter_shifts")


def load_case() -> dict:
    return yaml.safe_load((ROOT / "config/healthcare/value-case.yaml").read_text())


def value_case(store, semantic: SemanticLayer) -> list[dict]:
    case = load_case()
    window = case["measurement_window_days"]
    base = semantic.query(store, [k["metric"] for k in case["kpis"]])[0]
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
                    "ledger_id": f"VL-HC-{n:03d}",
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
    return finops.ai_cost(prompt_tokens, output_tokens, len(WARDS), HORIZON)


def focus(est: finops.CostEstimate, ledger_id: str = "VL-HC-001") -> list[dict]:
    tags = {"use-case": "healthcare-inpatient-fall-prevention", "domain": "healthcare", "value-ledger": ledger_id, "env": "simulation"}
    return finops.focus_rows(est, HORIZON, tags, "ba-halsey-vale-health", "hv-data-products")

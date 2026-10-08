"""Unfair-discrimination check on claim outcomes across synthetic postcode proxy groups.

A screening heuristic on synthetic data, not a legal test. For each decision the assistant drives (fast
track, leakage review, subrogation referral) it compares the selection rate of group G2 with group G1,
the reference group, and flags a ratio outside the band in config/insurance/fairness.yaml (0.80 to 1.25,
the four-fifths heuristic). It also compares mean days from report to payment. The band and the gap
were written before any result and are reported hit or miss.

Two sources:

* `history`: gold.fairness_monitor, the last 180 days under the current rules (read from restricted
  silver by the pipeline; agents see only these aggregates, and only agent:claims-compliance).
* `forward`: the per-group counters of the forward value simulation, current rules versus the
  assistant, per replication, with 95% bootstrap intervals for the ratio and for the change in the
  ratio (paired by replication).

The proxy group is never a model feature. Days from loss to report is, and it is correlated with the
group, which is exactly the path this check is meant to catch.
"""

from __future__ import annotations

import numpy as np
import yaml

from adl import ROOT
from adl.domains.insurance import simulate as SIM

DECISIONS = {"fast_track": ("fast_tracked", "assigned"), "leakage_review": ("reviews", "paid"), "subrogation_referral": ("referrals", "paid")}


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config/insurance/fairness.yaml").read_text())


def _rate(r: SIM.RunResult, num: str, den: str, g: str) -> float:
    return r.groups[f"{num}_{g}"] / max(r.groups[f"{den}_{g}"], 1)


def _per_run(runs: list[SIM.RunResult], decision: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if decision == "cycle_days":
        g1 = np.array([_rate(r, "cycle_days", "paid", "G1") for r in runs])
        g2 = np.array([_rate(r, "cycle_days", "paid", "G2") for r in runs])
    else:
        num, den = DECISIONS[decision]
        g1 = np.array([_rate(r, num, den, "G1") for r in runs])
        g2 = np.array([_rate(r, num, den, "G2") for r in runs])
    return g1, g2, g2 / np.where(g1 > 0, g1, np.nan)


def forward(cmp: SIM.Comparison, arms: tuple[str, ...] = ("current rules", "agent")) -> list[dict]:
    cfg = load_config()
    lo_band, hi_band = cfg["selection_rate_ratio"]["min"], cfg["selection_rate_ratio"]["max"]
    out = []
    for decision in (*DECISIONS, "cycle_days"):
        base_ratio = _per_run(cmp.arms["current rules"], decision)[2]
        for arm in arms:
            g1, g2, ratio = _per_run(cmp.arms[arm], decision)
            ok = ~np.isnan(ratio)
            lo, hi = SIM.bootstrap_ci(ratio[ok]) if ok.any() else (float("nan"), float("nan"))
            row = {
                "decision": decision,
                "arm": arm,
                "g1": float(g1.mean()),
                "g2": float(g2.mean()),
                "ratio": float(np.nanmean(ratio)),
                "ratio_ci": (lo, hi),
            }
            if decision == "cycle_days":
                gap = g2 - g1
                row["gap_days"] = float(gap.mean())
                row["gap_ci"] = SIM.bootstrap_ci(gap)
                row["within_limit"] = bool(abs(gap.mean()) <= cfg["max_cycle_days_gap"])
            else:
                row["within_limit"] = bool(lo_band <= np.nanmean(ratio) <= hi_band)
                row["ci_within_band"] = bool(lo_band <= lo and hi <= hi_band)
            if arm != "current rules":
                both = ok & ~np.isnan(base_ratio)
                d = ratio[both] - base_ratio[both]
                row["ratio_change"] = float(d.mean())
                row["ratio_change_ci"] = SIM.bootstrap_ci(d)
            out.append(row)
    return out


def history(store) -> list[dict]:
    """gold.fairness_monitor with the same limits applied (history is the current rules only)."""
    cfg = load_config()
    lo_band, hi_band = cfg["selection_rate_ratio"]["min"], cfg["selection_rate_ratio"]["max"]
    rows = store.sql("SELECT * FROM gold.fairness_monitor ORDER BY decision, group_name")
    out = []
    for d in sorted({r["decision"] for r in rows}):
        g = {r["group_name"]: r for r in rows if r["decision"] == d}
        ratio = g["G2"]["ratio_to_reference"]
        if d == "cycle_days":
            ok = abs(g["G2"]["rate"] - g["G1"]["rate"]) <= cfg["max_cycle_days_gap"]
        else:
            ok = lo_band <= ratio <= hi_band
        out.append(
            {
                "decision": d,
                "g1": g["G1"]["rate"],
                "g2": g["G2"]["rate"],
                "ratio": ratio,
                "eligible_g2": g["G2"]["eligible"],
                "within_limit": bool(ok),
            }
        )
    return out


def all_within(rows: list[dict]) -> bool:
    return all(r["within_limit"] for r in rows)

"""Fairness check on preventive measures and fall outcomes across synthetic patient groups.

A screening heuristic on synthetic data, not a legal or regulatory test. For each measure the assistant
proposes (bed alarm, hourly rounding, mobility aid, sitter) it compares the share of group G2 patients
given the measure with group G1, the reference group, and flags a ratio outside the band in
config/healthcare/fairness.yaml (0.80 to 1.25, the four-fifths heuristic). It also checks equal
opportunity (of the patients who fell, the share with a measure in place that day) and the gap in falls
per 1,000 bed-days. The limits were committed before any build or result and are reported hit or miss.

Two sources:

* `history`: gold.fairness_monitor, the last 180 days under the current rules (built from restricted
  silver by the pipeline; agents see only these aggregates, and only agent:health-equity).
* `forward`: the per-group counters of the forward value simulation, current rules versus the
  assistant, per replication, with 95% bootstrap intervals for the ratio and for the change in the
  ratio (paired by replication).

The group is never a model feature. Confusion is documented less often for G2 in the simulated wards,
and documented confusion is a feature, which is exactly the path this check is meant to catch.
"""

from __future__ import annotations

import numpy as np
import yaml

from adl import ROOT
from adl.domains.healthcare import simulate as SIM

# decision name in config/healthcare/fairness.yaml -> (numerator, denominator) among the simulation's group counters
DECISIONS = {
    "bed_alarm": ("bed_alarm", "patients"),
    "hourly_rounding": ("hourly_rounding", "patients"),
    "mobility_aid": ("mobility_aid", "patients"),
    "sitter_request": ("sitter", "patients"),
    "protected_before_fall": ("falls_protected", "falls"),
}
HISTORY_NAMES = {"sitter": "sitter_request"}  # gold.fairness_monitor names the measure as recorded


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config/healthcare/fairness.yaml").read_text())


def _rate(r: SIM.RunResult, num: str, den: str, g: str) -> float:
    return r.groups[f"{num}_{g}"] / r.groups[f"{den}_{g}"] if r.groups[f"{den}_{g}"] > 0 else float("nan")


def _per_run(runs: list[SIM.RunResult], decision: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if decision == "falls_per_1000_bed_days":
        g1 = np.array([1000 * _rate(r, "falls", "bed_days", "G1") for r in runs])
        g2 = np.array([1000 * _rate(r, "falls", "bed_days", "G2") for r in runs])
    else:
        num, den = DECISIONS[decision]
        g1 = np.array([_rate(r, num, den, "G1") for r in runs])
        g2 = np.array([_rate(r, num, den, "G2") for r in runs])
    ratio = np.where((g1 > 0) & ~np.isnan(g1) & ~np.isnan(g2), g2 / np.where(g1 > 0, g1, 1), np.nan)
    return g1, g2, ratio


def forward(cmp: SIM.Comparison, arms: tuple[str, ...] = ("current rules", "agent")) -> list[dict]:
    cfg = load_config()
    lo_band, hi_band = cfg["selection_rate_ratio"]["min"], cfg["selection_rate_ratio"]["max"]
    out = []
    for decision in (*DECISIONS, "falls_per_1000_bed_days"):
        base_ratio = _per_run(cmp.arms["current rules"], decision)[2]
        for arm in arms:
            g1, g2, ratio = _per_run(cmp.arms[arm], decision)
            ok = ~np.isnan(ratio)
            lo, hi = SIM.bootstrap_ci(ratio[ok]) if ok.sum() > 1 else (float("nan"), float("nan"))
            row = {
                "decision": decision,
                "arm": arm,
                "g1": float(np.nanmean(g1)),
                "g2": float(np.nanmean(g2)),
                "ratio": float(np.nanmean(ratio)) if ok.any() else float("nan"),
                "ratio_ci": (lo, hi),
                "replications_with_ratio": int(ok.sum()),
            }
            if decision == "falls_per_1000_bed_days":
                gap = g2 - g1
                row["gap"] = float(gap.mean())
                row["gap_ci"] = SIM.bootstrap_ci(gap)
                row["within_limit"] = bool(abs(gap.mean()) <= cfg["max_falls_per_1000_gap"])
            elif not ok.any():
                row["within_limit"] = True  # the measure was never used in this arm: no selection to compare
                row["not_used"] = True
            else:
                row["within_limit"] = bool(lo_band <= row["ratio"] <= hi_band)
                row["ci_within_band"] = bool(lo_band <= lo and hi <= hi_band)
            if arm != "current rules":
                both = ok & ~np.isnan(base_ratio)
                d = ratio[both] - base_ratio[both]
                if len(d) > 1:
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
        if d == "falls_per_1000_bed_days":
            ok = abs(g["G2"]["rate"] - g["G1"]["rate"]) <= cfg["max_falls_per_1000_gap"]
        else:
            ok = lo_band <= ratio <= hi_band
        out.append(
            {
                "decision": HISTORY_NAMES.get(d, d),
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

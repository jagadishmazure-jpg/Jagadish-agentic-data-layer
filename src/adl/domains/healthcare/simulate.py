"""Forward value simulation: the current rules versus the assistant's policy over the next 28 days.

Each replication starts from the real end-of-history state (every patient in hospital, their measures,
latest assessments and observations) and plays days 180 to 207 once per policy with the same new
admissions and the same random draws (common random numbers keyed by the replication seed and the day).
Four more arms switch on one measure at a time, which attributes the value to bed alarms, hourly
rounding, mobility aids or sitters.

Outcomes are measured against the simulator's ground truth (falls and harm as they happen in the
simulated wards), which is legitimate here because this is an evaluation harness, not product code.
Differences are paired by replication; intervals are 95% percentile bootstrap intervals over
replications. Net value is minus the cost of falls and measures at the stated assumptions, so its change
is the saving against the current rules. The same runs feed the fairness audit (measures and outcomes
by synthetic group). Everything here is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.domains.healthcare import world as W

EVAL_SEEDS = tuple(range(3000, 3030))  # reported: 30 replications
TUNING_SEEDS = tuple(range(4000, 4010))  # used only to choose risk_multiplier in config/healthcare/policy.yaml
OUTCOMES = (
    "net_value_usd", "fall_cost_usd", "harm_fall_cost_usd", "measure_cost_usd", "falls", "harm_falls", "falls_per_1000_bed_days",
    "time_to_intervention_days", "sitter_shifts", "bed_alarm_days", "rounding_days", "aid_starts", "bed_days",
)  # fmt: skip


@dataclass
class RunResult:
    policy: str
    seed: int
    outcomes: dict[str, float]
    groups: dict[str, float]


def group_counts(w: W.World, st: W.State) -> dict[str, float]:
    """Patients in hospital during the run and the share given each measure, by synthetic group, plus the
    group falls counters from the totals."""
    out = {}
    g = w.patients.group
    for k, name in enumerate(W.GROUPS):
        m = st.present & (g == k)
        out[f"patients_{name}"] = float(m.sum())
        for flag, measure in (("had_alarm", "bed_alarm"), ("had_rounding", "hourly_rounding"), ("had_aid", "mobility_aid"), ("had_sitter", "sitter")):
            out[f"{measure}_{name}"] = float((getattr(st, flag) & m).sum())
        for key in ("bed_days", "falls", "falls_protected"):
            out[f"{key}_{name}"] = float(st.totals[f"{key}_{name}"])
    return out


def play(base: W.World, start: W.State, policy: W.Policy, seed: int, name: str) -> RunResult:
    w = W.extend_world(base, seed)
    st = W.start_run(W.resize(start.copy(), w))
    for t in range(W.DAYS_HISTORY, W.DAYS_TOTAL):
        W.step(w, st, t, policy, seed)
    tot = st.totals
    out = {
        "net_value_usd": -(tot["fall_cost_usd"] + tot["measure_cost_usd"]),
        "fall_cost_usd": tot["fall_cost_usd"],
        "harm_fall_cost_usd": tot["harm_fall_cost_usd"],
        "measure_cost_usd": float(tot["measure_cost_usd"]),
        "falls": tot["falls"],
        "harm_falls": tot["harm_falls"],
        "falls_per_1000_bed_days": 1000 * tot["falls"] / max(tot["bed_days"], 1),
        "time_to_intervention_days": tot["first_measure_delay_days"] / max(tot["first_measures"], 1),
        "sitter_shifts": tot["sitter_shifts"],
        "bed_alarm_days": tot["alarm_days"],
        "rounding_days": tot["rounding_days"],
        "aid_starts": tot["aid_starts"],
        "bed_days": tot["bed_days"],
    }
    return RunResult(name, seed, out, group_counts(w, st))


def bootstrap_ci(diffs: np.ndarray, n_boot: int = 2000, seed: int = 7) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = diffs[rng.integers(0, len(diffs), (n_boot, len(diffs)))].mean(1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


@dataclass
class Comparison:
    arms: dict[str, list[RunResult]]
    seeds: tuple[int, ...]

    def mean(self, arm: str, metric: str) -> float:
        return float(np.mean([r.outcomes[metric] for r in self.arms[arm]]))

    def diff(self, arm: str, metric: str, base: str = "current rules") -> tuple[float, float, float]:
        d = np.array([a.outcomes[metric] - b.outcomes[metric] for a, b in zip(self.arms[arm], self.arms[base], strict=True)])
        lo, hi = bootstrap_ci(d)
        return float(d.mean()), lo, hi


def compare(base: W.World, start: W.State, policies: dict[str, W.Policy], seeds: tuple[int, ...] = EVAL_SEEDS) -> Comparison:
    arms = {name: [play(base, start, p, s, name) for s in seeds] for name, p in policies.items()}
    return Comparison(arms, seeds)

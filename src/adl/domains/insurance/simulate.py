"""Forward value simulation: the current rules versus the assistant's policy over the next 28 days.

Each replication starts from the real end-of-history state (every open claim, its queue and remaining
work, every claim waiting for payment) and plays days 180 to 207 once per policy with the same new
claims and the same random draws (common random numbers keyed by the replication seed and the day).
Three more arms switch on one lever at a time, which attributes the value to triage, leakage review or
subrogation referral.

Outcomes are measured against the simulator's ground truth (true overpayments and recoverable claims),
which is legitimate here because this is an evaluation harness, not product code. Differences are
paired by replication; intervals are 95% percentile bootstrap intervals over replications. Net value is
recoveries minus overpayments paid, review and referral costs and the cost of reopened claims. The
same runs feed the fairness audit (selection rates and cycle days by synthetic proxy group).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.domains.insurance import world as W

EVAL_SEEDS = tuple(range(3000, 3030))  # reported: 30 replications
TUNING_SEEDS = tuple(range(4000, 4010))  # used only to choose the triage thresholds in config/insurance/policy.yaml
OUTCOMES = (
    "net_value_usd", "overpayment_paid_usd", "recoveries_usd", "review_cost_usd", "referral_cost_usd", "reopen_cost_usd",
    "leakage_usd", "cycle_days", "reopen_rate_pct", "backlog_spread_days",
)  # fmt: skip


@dataclass
class RunResult:
    policy: str
    seed: int
    outcomes: dict[str, float]
    actions: dict[str, int]
    groups: dict[str, float]


def play(base: W.World, start: W.State, policy: W.Policy, seed: int, name: str) -> RunResult:
    w = W.extend_world(base, seed)
    st = W.resize(start.copy(), w)
    for t in range(W.DAYS_HISTORY, W.DAYS_TOTAL):
        W.step(w, st, t, policy, seed)
    tot = st.totals
    paid = max(tot["paid"], 1)
    out = {
        "net_value_usd": tot["recoveries_usd"]
        - tot["overpayment_paid_usd"]
        - tot["review_cost_usd"]
        - tot["referral_cost_usd"]
        - tot["reopen_cost_usd"],
        "overpayment_paid_usd": tot["overpayment_paid_usd"],
        "recoveries_usd": tot["recoveries_usd"],
        "review_cost_usd": tot["review_cost_usd"],
        "referral_cost_usd": tot["referral_cost_usd"],
        "reopen_cost_usd": tot["reopen_cost_usd"],
        "leakage_usd": tot["overpayment_paid_usd"] + tot["missed_recovery_usd"],
        "cycle_days": tot["cycle_days"] / paid,
        "reopen_rate_pct": 100 * tot["reopened"] / paid,
        "backlog_spread_days": tot["backlog_spread"] / max(tot["days"], 1),
    }
    actions = {k: int(tot[k]) for k in ("assigned", "fast_tracked", "reviews", "referrals", "escalations", "paid")}
    groups = {k: float(v) for k, v in tot.items() if k.endswith(tuple(f"_{g}" for g in W.GROUPS))}
    return RunResult(name, seed, out, actions, groups)


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

    def actions(self, arm: str, key: str) -> float:
        return float(np.mean([r.actions[key] for r in self.arms[arm]]))


def compare(base: W.World, start: W.State, policies: dict[str, W.Policy], seeds: tuple[int, ...] = EVAL_SEEDS) -> Comparison:
    arms = {name: [play(base, start, p, s, name) for s in seeds] for name, p in policies.items()}
    return Comparison(arms, seeds)

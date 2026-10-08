"""Forward value simulation: the current rules versus the agents' policy over the next 28 days.

Each replication starts from the real end-of-history state (every active lock, its stage, documents
and contact history) and plays days 180 to 207 twice, once per policy, with the same new
applications, the same market path and the same borrower behaviour (common random numbers keyed by
the replication seed and the day). Two more arms switch on one lever at a time, which attributes
the value to outreach, document chasing or lock extensions.

Outcomes are measured against the simulator's ground truth (true fallout and hedge losses), which is
legitimate here because this is an evaluation harness, not product code. Differences are paired by
replication; intervals are 95% percentile bootstrap intervals over replications. Net value is gain
on sale minus hedge losses, extension fees and outreach cost.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.domains.mortgage import world as W

EVAL_SEEDS = tuple(range(3000, 3030))  # reported: 30 replications
OUTCOMES = (
    "gain_on_sale_usd",
    "hedge_loss_usd",
    "extension_cost_usd",
    "outreach_cost_usd",
    "net_value_usd",
    "pull_through_pct",
    "fallout_rate_pct",
    "cycle_days",
)


@dataclass
class RunResult:
    policy: str
    seed: int
    outcomes: dict[str, float]
    actions: dict[str, int]


def play(base: W.World, start: W.State, policy: W.Policy, seed: int, name: str) -> RunResult:
    w = W.extend_world(base, seed)
    st = W.resize(start.copy(), w)
    for t in range(W.DAYS_HISTORY, W.DAYS_TOTAL):
        W.step(w, st, t, policy, seed)
    tot = st.totals
    resolved = tot["closed"] + tot["fallout"]
    out = {
        "gain_on_sale_usd": tot["gain_on_sale_usd"],
        "hedge_loss_usd": tot["hedge_loss_usd"],
        "extension_cost_usd": tot["extension_cost_usd"],
        "outreach_cost_usd": tot["outreach_cost_usd"],
        "net_value_usd": tot["gain_on_sale_usd"] - tot["hedge_loss_usd"] - tot["extension_cost_usd"] - tot["outreach_cost_usd"],
        "pull_through_pct": 100 * tot["closed"] / resolved if resolved else 0.0,
        "fallout_rate_pct": 100 * tot["fallout"] / resolved if resolved else 0.0,
        "cycle_days": tot["lock_to_close_days"] / tot["closed"] if tot["closed"] else 0.0,
    }
    actions = {"calls": int(tot["calls"]), "chases": int(tot["chases"]), "extensions": int(tot["extensions"])}
    return RunResult(name, seed, out, actions)


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

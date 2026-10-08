"""Forward value simulation: the current rules versus the agents' policy over the next 28 days.

Each replication starts from the real end-of-history state (stock by age, orders in transit) and plays
days 140 to 167 for every store-product series twice, once per policy, with identical shopper demand
and supplier behaviour (common random numbers keyed by the replication seed and the day). Two more
arms switch on one lever at a time, which attributes the value to replenishment or markdowns.

Outcomes are measured against the simulator's ground truth (true lost demand), which is legitimate
here because this is an evaluation harness, not product code. Differences are paired by replication;
intervals are 95% percentile bootstrap intervals over replications. Net value is gross margin after waste and
transfer costs, minus a carrying charge on closing stock, so holding more stock is not free.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.domains.retail import world as W

EVAL_SEEDS = tuple(range(2000, 2030))  # reported
TUNE_SEEDS = tuple(range(1000, 1010))  # used only while choosing policy settings
OUTCOMES = (
    "revenue_usd",
    "lost_sales_usd",
    "markdown_usd",
    "waste_cost_usd",
    "transfer_cost_usd",
    "gross_margin_usd",
    "closing_stock_usd",
    "holding_cost_usd",
    "net_value_usd",
    "stockout_rate_pct",
)
CARRYING_RATE = 0.25  # annual cost of holding stock, as a share of its cost value


@dataclass
class RunResult:
    policy: str
    seed: int
    outcomes: dict[str, float]
    actions: dict[str, int]


def play(world: W.World, cal: W.Calendar, start: W.State, policy, seed: int, name: str) -> RunResult:
    st = start.copy()
    days = range(W.DAYS_HISTORY, W.DAYS_TOTAL)
    for t in days:
        W.step(world, cal, st, t, policy, seed)
    r = st.records
    cost = world.unit_cost
    revenue = sum(x.revenue.sum() for x in r)
    cogs = sum(((x.units_regular + x.units_md) * cost).sum() for x in r)
    waste = sum((x.waste * cost).sum() for x in r)
    transfer = sum(x.transfer_in.sum() for x in r) * W.TRANSFER_COST
    closing = float((st.inv.sum(1) * cost).sum() + (st.pipe.sum(1) * cost).sum())
    margin = revenue - cogs - waste - transfer
    out = {
        "revenue_usd": revenue,
        "lost_sales_usd": sum((x.lost_true * x.price).sum() for x in r),
        "markdown_usd": sum(x.md_amount.sum() for x in r),
        "waste_cost_usd": waste,
        "transfer_cost_usd": transfer,
        "gross_margin_usd": margin,
        "closing_stock_usd": closing,
        "holding_cost_usd": closing * CARRYING_RATE * W.HORIZON / 365,
        "net_value_usd": margin - closing * CARRYING_RATE * W.HORIZON / 365,
        "stockout_rate_pct": 100 * float(st.soldout[days.start : days.stop].mean()),
    }
    actions = {
        "purchase_order_lines": int(sum((x.order_qty > 0).sum() for x in r)),
        "markdowns": int(sum((x.discount > 0).sum() for x in r)),
        "transfers": int(sum((x.transfer_in > 0).sum() for x in r)),
        "units_ordered": int(sum(x.order_qty.sum() for x in r)),
    }
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


def compare(world: W.World, cal: W.Calendar, start: W.State, policies: dict[str, object], seeds: tuple[int, ...] = EVAL_SEEDS) -> Comparison:
    arms = {name: [play(world, cal, start, p, s, name) for s in seeds] for name, p in policies.items()}
    return Comparison(arms, seeds)

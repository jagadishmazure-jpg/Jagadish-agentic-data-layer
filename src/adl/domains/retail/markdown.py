"""Markdown optimisation: price elasticity from store price tests, then the smallest discount that clears.

Elasticity. The forecast regression has a price coefficient, but price only moves with promotions
(which also get a display) and small list-price changes, so that coefficient is biased towards zero.
The grocer's test-and-learn programme moves one product's shelf price in four stores for a week; a
difference-in-differences estimate per test (test stores versus the other four, test week versus the
two weeks before) isolates the price effect. The category elasticity is the median over its tests.

Markdown response. Today's rule marks every near-date unit down 30%. From history, `take30` is the
share of a day's typical demand that bought marked-down units on days when they did not run out. The
response at another discount d is extrapolated with the elasticity: take(d) = take30 * ((1-d)/0.7)^e.

Decision. For near-date units n and forecast f: no markdown if n <= take(0) * f; otherwise the smallest
discount on the grid (10% steps, at most 50%) whose expected take clears n; the deepest allowed if none
does. Anything deeper than the approval threshold (20% in config/policy.yaml) waits for a person.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from adl.domains.retail.features import Frame
from adl.domains.retail.forecast import levels
from adl.domains.retail.world import CATS

GRID = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
RULE_DISCOUNT = 0.30


@dataclass
class TestEstimate:
    sku: str
    category: str
    start_day: int
    multiplier: float
    elasticity: float


def elasticity_from_tests(f: Frame, tests: list[dict]) -> tuple[dict[str, float], list[TestEstimate]]:
    sku_cat = {k[1]: CATS[c] for k, c in zip(f.keys, f.cat, strict=True)}
    col = {k: i for i, k in enumerate(f.keys)}
    stores = sorted({k[0] for k in f.keys})
    groups: dict[tuple[str, int, float], list[str]] = defaultdict(list)
    for t in tests:
        groups[(t["sku"], t["start_day"], t["multiplier"])].append(t["store_id"])

    def mean_log(store_ids: list[str], sku: str, a: int, b: int) -> float:
        vals = []
        for s in store_ids:
            j = col[(s, sku)]
            u, so = f.units[a:b, j], f.soldout[a:b, j]
            ok = ~np.isnan(u) & ~so
            if ok.any():
                vals.append(u[ok].mean())
        return float(np.log(np.mean(vals) + 0.5))

    out = []
    for (sku, start, mult), test_stores in sorted(groups.items()):
        control = [s for s in stores if s not in test_stores]
        d_test = mean_log(test_stores, sku, start, start + 7) - mean_log(test_stores, sku, start - 14, start)
        d_ctrl = mean_log(control, sku, start, start + 7) - mean_log(control, sku, start - 14, start)
        out.append(TestEstimate(sku, sku_cat[sku], start, mult, (d_test - d_ctrl) / np.log(mult)))
    by_cat = {c: float(np.median([e.elasticity for e in out if e.category == c])) for c in CATS}
    return by_cat, out


def take30(f: Frame) -> dict[str, float]:
    """Share of typical daily demand that bought 30%-off near-date units, on days they did not run out."""
    lev = levels(f.units, f.soldout)
    out = {}
    for ci, c in enumerate(CATS):
        cols = np.nonzero(f.cat == ci)[0]
        ratios = []
        for t in range(29, f.units.shape[0]):
            md, avail, base = f.md_units[t, cols], f.near[t - 1, cols], lev[t - 1, cols]
            ok = (md > 0) & (md < avail) & (base > 0)
            ratios.extend((md[ok] / base[ok]).tolist())
        out[c] = float(np.median(ratios)) if ratios else 0.0
    return out


def take(d: np.ndarray, t30: np.ndarray, e: np.ndarray) -> np.ndarray:
    return t30 * ((1 - d) / (1 - RULE_DISCOUNT)) ** e


def choose(near: np.ndarray, fc: np.ndarray, t30: np.ndarray, e: np.ndarray, max_discount: float = 0.5) -> np.ndarray:
    """Discount per series (0 where no markdown is needed)."""
    grid = GRID[GRID <= max_discount + 1e-9]
    out = np.full(near.shape, grid[-1])
    cleared = np.zeros(near.shape, bool)
    for d in grid[1:]:
        ok = ~cleared & (take(np.full(near.shape, d), t30, e) * fc >= near)
        out[ok] = d
        cleared |= ok
    no_need = near <= take(np.zeros(near.shape), t30, e) * fc
    out[no_need | (near <= 0) | (t30 <= 0)] = 0.0
    return out

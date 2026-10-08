"""Stockout risk: the probability that a store-product sells out within the next three days.

For each day d of the window, the shelf sells out if demand over days 1..d exceeds the closing stock
plus the open purchase orders that will have arrived by then. A delivery due on a day is counted from
the following day: deliveries from the two least reliable suppliers are on time only about two times in
three, and a delivery arriving mid-afternoon does not save the morning. Demand is treated as normal
with the forecast as its mean and a per-category relative error from training; the risk is the largest
of the three daily probabilities. The comparison is the rule a store team uses today: flag anything
with fewer than three days of cover at the 7-day average.

The backtest scores both on 32 consecutive origins (days 105 to 136) against what happened.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from adl.domains.retail.features import Frame
from adl.domains.retail.forecast import Forecaster, levels

HORIZON = 3
_erf = np.vectorize(math.erf)


def normal_cdf(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1 + _erf(z / math.sqrt(2)))


def probability(fc: np.ndarray, cv: np.ndarray, available: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """P(demand over the window > available). fc is (S, H) forecasts, cv the per-series relative error."""
    mu = fc.sum(1)
    sd = np.sqrt(((cv[:, None] * fc) ** 2).sum(1) + mu) + 1e-6
    return 1 - normal_cdf((available + 0.5 - mu) / sd), mu


def window_probability(fc: np.ndarray, cv: np.ndarray, on_hand: np.ndarray, due: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Largest daily sell-out probability over the window. due is (S, H): units due on each day."""
    ps = []
    for d in range(1, fc.shape[1] + 1):
        arrived = due[:, : d - 1].sum(1)
        p, _ = probability(fc[:, :d], cv, on_hand + arrived)
        ps.append(p)
    return np.max(ps, 0), fc.sum(1)


def due_by_day(po_rows: list[dict], keys: list[tuple[str, str]], origin: int, horizon: int = HORIZON) -> np.ndarray:
    """Units on open orders due on each day of the window (an overdue order counts as due tomorrow)."""
    idx = {k: i for i, k in enumerate(keys)}
    out = np.zeros((len(keys), horizon))
    for r in po_rows:
        if r["order_day"] > origin or (r["received_day"] is not None and r["received_day"] <= origin):
            continue
        due = max(r["expected_day"], origin + 1)
        if due <= origin + horizon:
            out[idx[(r["store_id"], r["sku"])], due - origin - 1] += r["qty_ordered"]
    return out


def inbound(po_rows: list[dict], keys: list[tuple[str, str]], origin: int, horizon: int = HORIZON) -> np.ndarray:
    """Units on open orders (placed by the origin, not yet received) expected inside the window."""
    idx = {k: i for i, k in enumerate(keys)}
    out = np.zeros(len(keys))
    for r in po_rows:
        if r["order_day"] > origin or (r["received_day"] is not None and r["received_day"] <= origin):
            continue
        due = max(r["expected_day"], origin + 1)
        if due <= origin + horizon:
            out[idx[(r["store_id"], r["sku"])]] += r["qty_ordered"]
    return out


def band(p: np.ndarray) -> np.ndarray:
    return np.where(p >= 0.5, "high", np.where(p >= 0.2, "medium", "low"))


def auc(y: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    # average ranks for ties
    s_sorted = score[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j + 2) / 2
        i = j + 1
    pos = y.sum()
    neg = len(y) - pos
    return float((ranks[y].sum() - pos * (pos + 1) / 2) / max(pos * neg, 1))


@dataclass
class RiskBacktest:
    rows: list[dict]
    samples: int
    positives: int


def backtest(f: Frame, po_rows: list[dict], origins=range(105, 137)) -> RiskBacktest:
    lev = levels(f.units, f.soldout)
    ys, ps, naive = [], [], []
    for o in origins:
        model = Forecaster.fit(f, o)
        fc = model.predict(f, o, HORIZON, lev[o])
        p, _ = window_probability(fc, model.cv[f.cat], f.on_hand[o], due_by_day(po_rows, f.keys, o))
        avg7 = np.nanmean(f.units[o - 6 : o + 1], 0)
        y = f.soldout[o + 1 : o + 1 + HORIZON].any(0)
        ys.append(y)
        ps.append(p)
        naive.append(f.on_hand[o] / np.maximum(avg7, 0.1) < HORIZON)
    y = np.concatenate(ys)
    p = np.concatenate(ps)
    nv = np.concatenate(naive)

    def scores(pred: np.ndarray) -> dict:
        tp = int((pred & y).sum())
        fp = int((pred & ~y).sum())
        fn = int((~pred & y).sum())
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        return {
            "flagged": int(pred.sum()),
            "precision_pct": 100 * prec,
            "recall_pct": 100 * rec,
            "f1_pct": 100 * (2 * prec * rec / max(prec + rec, 1e-9)),
        }

    rows = [
        {"method": "risk model (p >= 0.5)", **scores(p >= 0.5), "auc": auc(y, p), "brier": float(np.mean((p - y) ** 2))},
        {"method": "days-of-cover rule (< 3 days)", **scores(nv), "auc": float("nan"), "brier": float("nan")},
    ]
    return RiskBacktest(rows, len(y), int(y.sum()))

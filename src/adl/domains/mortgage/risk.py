"""Fallout risk: the probability that an active rate lock falls out (withdrawn, expired or declined)
within the next 14 days.

Features are process signals the lender observes on the morning of the decision: days to lock
expiry, how far the market has moved against the lock, outstanding documents and how fast the
borrower returns them, contact recency and unanswered calls, stage and time in stage, extensions,
channel, purpose and product. No credit attribute, protected characteristic or proxy for one is a
feature, and the product must never be used to approve, decline or price a loan (its contract
prohibits credit decisioning).

The model is an L2-regularised logistic regression fitted by Newton's method on standardised
features. The backtest trains on origins 30..136 (labels end on day 149) and scores origins
150..165 (labels end on day 178), so no test outcome is seen in training. The comparison is
today's rule, "call everyone within 10 days of expiry, closest first": both pick the 60 locks the
loan officers can call each day, and the backtest counts how many of the locks that went on to
fall out each one picked.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.domains.mortgage import world as W

HORIZON = 14
TRAIN_ORIGINS = range(30, 137)
TEST_ORIGINS = range(150, 166)
FIT_ORIGINS = range(30, W.DAYS_HISTORY - HORIZON + 1)  # every origin whose 14-day outcome is observed
FEATURES = (
    "days_to_expiry",
    "near_expiry",
    "rate_gap_bps",
    "market_below_lock_bps",
    "docs_outstanding",
    "doc_return_rate",
    "days_since_contact",
    "unanswered_calls_7d",
    "stage",
    "days_in_stage",
    "extensions",
    "broker",
    "direct",
    "refinance",
    "jumbo",
    "government",
)
DRIVERS = {  # feature: (label, the side of the average that the label describes)
    "rate_gap_bps": ("market rate below the lock", 1),
    "market_below_lock_bps": ("market rate below the lock", 1),
    "docs_outstanding": ("documents outstanding", 1),
    "doc_return_rate": ("slow document return", -1),
    "days_since_contact": ("no recent contact", 1),
    "unanswered_calls_7d": ("unanswered calls", 1),
    "days_in_stage": ("stalled in stage", 1),
    "near_expiry": ("lock expiring", 1),
    "days_to_expiry": ("lock expiring", -1),
    "refinance": ("refinance borrower", 1),
    "broker": ("broker channel", 1),
    "direct": ("direct online channel", 1),
    "extensions": ("already extended", 1),
}
GOS = np.array([W.GAIN_ON_SALE[p] for p in W.PRODUCTS])


def features(v: W.View) -> np.ndarray:
    t = v.t
    gap = (v.locked_rate - v.market_rate) * 100
    since_lock = np.maximum(t - v.lock_day, 1)
    cols = [
        v.expiry - t,
        (v.expiry - t <= 10).astype(float),
        gap,
        np.maximum(gap, 0),
        v.docs_out,
        (v.n_cond - v.docs_out) / since_lock,
        np.minimum(t - v.last_contact, 30),
        v.unanswered_7d,
        v.stage,
        np.minimum(t - v.stage_since, 40),
        v.extensions,
        v.channel == W.CHANNELS.index("broker"),
        v.channel == W.CHANNELS.index("direct"),
        v.purpose == W.PURPOSES.index("refinance"),
        v.product == W.PRODUCTS.index("jumbo"),
        np.isin(v.product, [W.PRODUCTS.index("fha"), W.PRODUCTS.index("va")]),
    ]
    return np.column_stack([np.asarray(c, float) for c in cols])


def value_at_risk(v: W.View) -> np.ndarray:
    """Gain on sale the lender loses if the loan falls out, plus the hedge loss if the market has fallen."""
    return v.amount * GOS[v.product] + v.amount * np.maximum(v.locked_rate - v.market_rate, 0) * W.HEDGE_DURATION / 100


@dataclass
class FalloutModel:
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 25) -> FalloutModel:
        mean, scale = X.mean(0), X.std(0) + 1e-9
        Z = np.column_stack([np.ones(len(X)), (X - mean) / scale])
        w = np.zeros(Z.shape[1])
        reg = np.full(Z.shape[1], l2)
        reg[0] = 0.0
        for _ in range(iters):
            p = 1 / (1 + np.exp(-Z @ w))
            g = Z.T @ (p - y) + reg * w
            H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg)
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        return cls(mean, scale, w[1:], float(w[0]))

    def predict(self, X: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-(self.intercept + ((X - self.mean) / self.scale) @ self.coef)))

    def drivers(self, X: np.ndarray) -> list[str]:
        """The feature that pushes each row's risk up the most (coefficient x standardised value), named only
        when the value sits on the side of the average that its label describes."""
        z = (X - self.mean) / self.scale
        names = list(FEATURES)
        keep = [names.index(k) for k in DRIVERS]
        side = np.array([DRIVERS[names[j]][1] for j in keep])
        contrib = (z * self.coef)[:, keep]
        contrib = np.where((np.sign(z[:, keep]) == side) & (contrib > 0), contrib, -np.inf)
        best = np.argmax(contrib, 1)
        return [DRIVERS[names[keep[b]]][0] if np.isfinite(contrib[i, b]) else "no single driver" for i, b in enumerate(best)]


def band(p: np.ndarray) -> list[str]:
    return ["high" if x >= 0.15 else "medium" if x >= 0.05 else "low" for x in p]


def labels(store, ids: np.ndarray, t: int) -> np.ndarray:
    """1 if the application fell out on days t..t+13 (from silver.stage_events)."""
    rows = store.sql(
        "SELECT application_id FROM silver.stage_events WHERE stage IN ('withdrawn', 'expired', 'denied') AND day BETWEEN ? AND ?",
        [t, t + HORIZON - 1],
    )
    out = {int(r["application_id"][4:]) - 1 for r in rows}
    return np.array([i in out for i in ids], float)


def dataset(store, origins: range) -> tuple[list[W.View], np.ndarray, np.ndarray]:
    from adl.domains.mortgage.pipeline import observe

    views, Xs, ys = [], [], []
    for t in origins:
        v = observe(store, t).view
        views.append(v)
        Xs.append(features(v))
        ys.append(labels(store, v.idx, t))
    return views, np.vstack(Xs), np.concatenate(ys)


def auc(score: np.ndarray, y: np.ndarray) -> float:
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score))
    s = score[order]
    i = 0
    while i < len(s):  # average ranks for ties
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


@dataclass
class Backtest:
    rows: list[dict]
    origins: int
    locks_scored: int
    fallout_rate_pct: float
    model: FalloutModel


def backtest(store) -> Backtest:
    _, Xtr, ytr = dataset(store, TRAIN_ORIGINS)
    m = FalloutModel.fit(Xtr, ytr)
    views, Xte, yte = dataset(store, TEST_ORIGINS)
    p = m.predict(Xte)
    dte = Xte[:, 0]
    out = {"fallout model": {"tp": 0, "picked": 0, "var": 0.0}, "within 10 days, closest first": {"tp": 0, "picked": 0, "var": 0.0}}
    k = 0
    falls, var_total = 0, 0.0
    for v in views:
        n = len(v.idx)
        pp, yy, dd = p[k : k + n], yte[k : k + n], dte[k : k + n]
        var = value_at_risk(v)
        falls += int(yy.sum())
        var_total += float((var * yy).sum())
        picks = {
            "fallout model": np.argsort(-(pp * var), kind="mergesort")[: W.CALL_CAPACITY],
            "within 10 days, closest first": [i for i in np.lexsort((v.idx, dd)) if dd[i] <= 10][: W.CALL_CAPACITY],
        }
        for name, sel in picks.items():
            sel = np.asarray(sel, int)
            out[name]["tp"] += int(yy[sel].sum())
            out[name]["picked"] += len(sel)
            out[name]["var"] += float((var[sel] * yy[sel]).sum())
        k += n
    base_rate = float(ytr.mean())
    rows = []
    for name, r in out.items():
        score = p if name == "fallout model" else -dte
        rows.append(
            {
                "method": name,
                "auc": auc(score, yte),
                "precision_pct": 100 * r["tp"] / max(r["picked"], 1),
                "recall_pct": 100 * r["tp"] / max(falls, 1),
                "value_at_risk_covered_pct": 100 * r["var"] / max(var_total, 1e-9),
                "brier": float(np.mean((p - yte) ** 2)) if name == "fallout model" else float(np.mean((base_rate - yte) ** 2)),
            }
        )
    return Backtest(rows, len(TEST_ORIGINS), len(yte), 100 * float(yte.mean()), m)


def fit_production(store) -> FalloutModel:
    _, X, y = dataset(store, FIT_ORIGINS)
    return FalloutModel.fit(X, y)

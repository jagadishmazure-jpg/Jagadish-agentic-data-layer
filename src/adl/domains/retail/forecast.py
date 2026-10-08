"""Demand forecast: a global ridge regression on log demand, backtested against naive baselines.

One model for all 384 store-product series, trained only on days that did not sell out (a sold-out
day records sales, not demand, and training on it teaches the model to under-forecast). Features are
known in advance: the series' recent level, weekday, promotion, price ratio by category (the
coefficient is the estimated price elasticity), temperature by category, rain and local events by
category. Predictions are converted back from log space with Duan's smearing factor per category.

The backtest is rolling-origin: four origins, each forecasting the next seven days from information up
to the origin, scored on days that did not sell out against two baselines (same day last week, and the
28-day mean). Numbers come from `adl forecast`; nothing is tuned on the backtest window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.domains.retail.features import Frame
from adl.domains.retail.world import CATS

LEVEL_DAYS = 28
MAX_H = 10
RIDGE = 1.0
ORIGINS = (111, 118, 125, 132)
FEATURES = (
    ["intercept", "log_level"]
    + [f"dow_{d}" for d in range(1, 7)]
    + ["promo"]
    + [f"log_price_x_{c}" for c in CATS]
    + [f"temp_x_{c}" for c in CATS]
    + ["rain"]
    + [f"event_x_{c}" for c in CATS]
    + ["markdown_day"]
)


def levels(units: np.ndarray, soldout: np.ndarray) -> np.ndarray:
    """level[o, s]: mean of unconstrained units over days o-27..o (NaN-safe; 0 if none)."""
    ok = ~np.isnan(units) & ~soldout
    u = np.where(ok, units, 0.0)
    cs_u = np.vstack([np.zeros(units.shape[1]), np.cumsum(u, 0)])
    cs_n = np.vstack([np.zeros(units.shape[1]), np.cumsum(ok, 0)])
    o = np.arange(units.shape[0])
    lo = np.maximum(o - LEVEL_DAYS + 1, 0)
    tot = cs_u[o + 1] - cs_u[lo]
    cnt = cs_n[o + 1] - cs_n[lo]
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), 0.0)


def design(f: Frame, t: np.ndarray, s: np.ndarray, level: np.ndarray, md: np.ndarray | None = None) -> np.ndarray:
    n = len(t)
    cat1 = np.zeros((n, len(CATS)))
    cat1[np.arange(n), f.cat[s]] = 1.0
    dow = np.zeros((n, 6))
    d = t % 7
    dow[d > 0, d[d > 0] - 1] = 1.0
    logp = np.log(f.price_ratio[t, s])[:, None] * cat1
    temp = f.temp[t, s][:, None] * cat1
    ev = f.event[t, s].astype(float)[:, None] * cat1
    cols = [np.ones((n, 1)), np.log1p(level)[:, None], dow, f.promo[t, s].astype(float)[:, None], logp, temp, f.rain[t, s].astype(float)[:, None], ev]
    cols.append((md if md is not None else np.zeros(n))[:, None])
    return np.hstack(cols)


@dataclass
class Forecaster:
    coef: np.ndarray
    smear: np.ndarray  # per category
    cv: np.ndarray  # per category, relative error in units
    trained_through: int
    rows: int

    @classmethod
    def fit(cls, f: Frame, upto: int) -> Forecaster:
        lev = levels(f.units, f.soldout)
        ts, ss = np.meshgrid(np.arange(LEVEL_DAYS + MAX_H, upto + 1), np.arange(f.n), indexing="ij")
        ts, ss = ts.ravel(), ss.ravel()
        h = 1 + (ts + ss) % MAX_H
        o = ts - h
        y = f.units[ts, ss]
        keep = ~np.isnan(y) & ~f.soldout[ts, ss]
        ts, ss, o, y = ts[keep], ss[keep], o[keep], y[keep]
        md = (f.md_units[ts, ss] > 0).astype(float)
        x = design(f, ts, ss, lev[o, ss], md)
        yy = np.log1p(y)
        a = x.T @ x + RIDGE * np.eye(x.shape[1])
        a[0, 0] -= RIDGE  # do not shrink the intercept
        coef = np.linalg.solve(a, x.T @ yy)
        resid = yy - x @ coef
        cats = f.cat[ss]
        smear = np.array([np.mean(np.exp(resid[cats == c])) for c in range(len(CATS))])
        pred = np.maximum(np.exp(x @ coef) * smear[cats] - 1, 0.0)
        cv = np.array([np.sqrt(np.mean(((y - pred)[cats == c] / np.maximum(pred[cats == c], 1.0)) ** 2)) for c in range(len(CATS))])
        return cls(coef, smear, cv, upto, len(y))

    def predict(self, f: Frame, origin: int, horizon: int, level: np.ndarray) -> np.ndarray:
        """(S, horizon) forecasts for days origin+1 .. origin+horizon, given each series' level at the origin."""
        out = np.zeros((f.n, horizon))
        s = np.arange(f.n)
        for h in range(1, horizon + 1):
            t = np.full(f.n, origin + h)
            x = design(f, t, s, level)
            out[:, h - 1] = np.maximum(np.exp(x @ self.coef) * self.smear[f.cat] - 1, 0.0)
        return out

    def elasticity(self) -> dict[str, float]:
        return {c: float(self.coef[FEATURES.index(f"log_price_x_{c}")]) for c in CATS}


def wape(y: np.ndarray, p: np.ndarray) -> float:
    return float(100 * np.abs(y - p).sum() / max(y.sum(), 1e-9))


def bias(y: np.ndarray, p: np.ndarray) -> float:
    return float(100 * (p - y).sum() / max(y.sum(), 1e-9))


@dataclass
class Backtest:
    rows: list[dict]  # per model: wape, bias, n
    by_category: list[dict]
    per_origin: list[dict]


def backtest(f: Frame, origins: tuple[int, ...] = ORIGINS, horizon: int = 7) -> Backtest:
    lev = levels(f.units, f.soldout)
    ys, preds, cats = [], {"ridge model": [], "same day last week": [], "28-day mean": []}, []
    per_origin = []
    for o in origins:
        model = Forecaster.fit(f, o)
        fc = model.predict(f, o, horizon, lev[o])
        oy, op, on = [], [], []
        for h in range(1, horizon + 1):
            t = o + h
            y = f.units[t]
            ok = ~np.isnan(y) & ~f.soldout[t]
            naive = f.units[t - 7]
            ok &= ~np.isnan(naive)
            ys.append(y[ok])
            cats.append(f.cat[ok])
            preds["ridge model"].append(fc[ok, h - 1])
            preds["same day last week"].append(naive[ok])
            preds["28-day mean"].append(lev[o][ok])
            oy.append(y[ok])
            op.append(fc[ok, h - 1])
            on.append(naive[ok])
        oy, op, on = np.concatenate(oy), np.concatenate(op), np.concatenate(on)
        per_origin.append({"origin": o, "days": f"{o + 1}-{o + horizon}", "model_wape": wape(oy, op), "naive_wape": wape(oy, on), "points": len(oy)})
    y = np.concatenate(ys)
    c = np.concatenate(cats)
    rows, by_cat = [], []
    for name, p in preds.items():
        p = np.concatenate(p)
        rows.append({"model": name, "wape_pct": wape(y, p), "bias_pct": bias(y, p), "points": len(y)})
        for ci, cn in enumerate(CATS):
            m = c == ci
            by_cat.append({"model": name, "category": cn, "wape_pct": wape(y[m], p[m])})
    return Backtest(rows, by_cat, per_origin)

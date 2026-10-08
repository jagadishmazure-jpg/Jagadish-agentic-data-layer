"""The fall-risk model, backtested against the Morse rule Halsey Vale uses today.

**Fall in the next three days** (`FallModel`): the probability that a patient in hospital on a morning
falls that day or in the two days after, from what the nurses record: the latest Morse items and how
old that assessment is, yesterday's observations (confusion, night restlessness, toileting calls,
unsteady gait), the medication record's sedation flag (a flag, no drug names), age band, ward type,
admission source, days in hospital and whether the patient has a walking aid. Labels come from the
incident reporting tool (`silver.fall_incidents`). Compared with the Morse fall scale: its total as a
ranking, and the rule "45 or more is high risk".

The model sees no name, record number or synthetic group. Confusion that is not documented is a
feature of its own (`confusion_not_documented`), because documentation is uneven (the fairness audit
checks the decisions the score drives). The labels were recorded while the current rules' measures
were in place, so the model learns risk under today's care, not untreated risk (a limitation stated in
docs/healthcare/fall-risk-model.md).

This is decision support for a nurse's preventive measures. It is not a medical device, it is not
validated for clinical use, and it never recommends a medication, a diagnosis or a treatment.

An L2-regularised logistic regression (adl.core.logit). Training uses mornings up to a cut-off whose
labels were observed by then; testing uses later mornings whose labels were observed by day 179.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.core.logit import Logistic, auc
from adl.domains.healthcare import pipeline as P
from adl.domains.healthcare import world as W

FEATURES = (
    "age_band", "elective", "transfer", "surgical_ward", "orthopaedic_ward", "elderly_care_ward", "neurology_ward", "cardiology_ward", "log_days_in", "first_two_days",
    "morse_history", "morse_secondary_dx", "morse_aid_stick_frame", "morse_aid_furniture", "morse_iv", "morse_gait_weak", "morse_gait_impaired",
    "morse_forgets_limits", "days_since_assessment", "confusion_yes", "confusion_not_documented", "night_restless", "toileting_calls",
    "unsteady_gait", "sedation_flag", "has_walking_aid",
)  # fmt: skip
DRIVERS = {
    0: ("older age band", 1), 9: ("first days after admission", 1), 10: ("fell in the last three months", 1), 16: ("Morse: impaired gait", 1),
    17: ("Morse: forgets limitations", 1), 19: ("confusion documented yesterday", 1), 21: ("restless at night", 1),
    22: ("frequent toileting calls", 1), 23: ("unsteady gait observed", 1), 24: ("recent sedation flag", 1),
}  # fmt: skip
LABEL_DAYS = 3  # a fall on the morning's day or the two days after
TRAIN_MORNINGS, TEST_MORNINGS = range(10, 118), range(123, W.DAYS_HISTORY - LABEL_DAYS + 1)
CAPACITY_K = W.CAPACITY["hourly_rounding"]  # patients a morning's worklist can act on: the rounding capacity
BANDS = (0.03, 0.08)  # risk band edges: low below 3%, high from 8%


def features(v: W.View) -> np.ndarray:
    if len(v.idx) == 0:
        return np.zeros((0, len(FEATURES)))
    m, o = v.morse, v.obs
    cols = [
        v.age,
        v.source == W.SOURCES.index("elective"),
        v.source == W.SOURCES.index("transfer"),
        *(v.ward % len(W.WARD_TYPES) == k for k in range(1, 6)),
        np.log1p(v.days_in),
        v.days_in <= 2,
        m[:, 0] == 25,
        m[:, 1] == 15,
        m[:, 2] == 15,
        m[:, 2] == 30,
        m[:, 3] == 20,
        m[:, 4] == 10,
        m[:, 4] == 20,
        m[:, 5] == 15,
        np.minimum(v.days_since_assessment, 3),
        o[:, 0] == 1,
        o[:, 0] == -1,
        o[:, 1],
        np.minimum(o[:, 2], 8),
        o[:, 3],
        o[:, 4],
        v.has_aid,
    ]
    return np.column_stack([np.asarray(c, float) for c in cols])


def band(p: np.ndarray) -> list[str]:
    return ["high" if x >= BANDS[1] else "medium" if x >= BANDS[0] else "low" for x in p]


@dataclass
class Mornings:
    """Patient-mornings rebuilt from silver with `pipeline.observe`, the same view the policies see."""

    X: np.ndarray
    y: np.ndarray
    morse: np.ndarray
    day: np.ndarray
    idx: np.ndarray


def mornings(store, days: range) -> Mornings:
    falls = {}
    for r in store.sql("SELECT encounter_id, day FROM silver.fall_incidents"):
        falls.setdefault(int(r["encounter_id"][4:]) - 1, []).append(int(r["day"]))
    Xs, ys, ms, ds, ids = [], [], [], [], []
    for t in days:
        v = P.observe(store, t).view
        Xs.append(features(v))
        ys.append([any(t <= d < t + LABEL_DAYS for d in falls.get(int(i), ())) for i in v.idx])
        ms.append(v.morse_total)
        ds.append(np.full(len(v.idx), t))
        ids.append(v.idx)
    return Mornings(np.vstack(Xs), np.concatenate(ys).astype(float), np.concatenate(ms), np.concatenate(ds), np.concatenate(ids))


@dataclass
class FallModel:
    model: Logistic
    harm_share: float  # share of falls in the incident history that caused harm (planning figure for the policy)

    def p(self, v: W.View) -> np.ndarray:
        return self.model.predict(features(v))

    def drivers(self, v: W.View) -> list[str]:
        return self.model.top_driver(features(v), DRIVERS)


def fit_production(store, data: Mornings | None = None) -> FallModel:
    data = data or mornings(store, range(10, W.DAYS_HISTORY - LABEL_DAYS + 1))
    harm = store.sql("SELECT avg(CAST(harm AS DOUBLE)) AS s FROM silver.fall_incidents")[0]["s"]
    return FallModel(Logistic.fit(data.X, data.y), round(float(harm), 4))


def _top_k_per_day(score: np.ndarray, day: np.ndarray, k: int) -> np.ndarray:
    sel = np.zeros(len(score), bool)
    for t in np.unique(day):
        rows = np.flatnonzero(day == t)
        sel[rows[np.argsort(-score[rows], kind="mergesort")[:k]]] = True
    return sel


@dataclass
class Backtest:
    rows: list[dict]
    sizes: dict[str, float]


def backtest(store) -> Backtest:
    tr = mornings(store, TRAIN_MORNINGS)
    te = mornings(store, TEST_MORNINGS)
    m = Logistic.fit(tr.X, tr.y)
    p = m.predict(te.X)
    y = te.y
    pos = y.sum()
    rows = []
    morse = te.morse.astype(float)
    for name, score, prob in (("fall-risk model", p, p), ("Morse total", morse + te.idx * 1e-9, None)):
        sel = _top_k_per_day(score, te.day, CAPACITY_K)
        rows.append(
            {
                "method": name,
                "auc": auc(score if prob is None else prob, y),
                "precision_at_capacity_pct": 100 * y[sel].mean(),
                "recall_at_capacity_pct": 100 * y[sel].sum() / pos,
                "brier": float(np.mean((prob - y) ** 2)) if prob is not None else float(np.mean((tr.y.mean() - y) ** 2)),
            }
        )
    high = te.morse >= 45
    rows.append(
        {
            "method": "Morse 45 or more (rule)",
            "auc": auc(high.astype(float), y),
            "precision_at_capacity_pct": 100 * y[high].mean(),
            "recall_at_capacity_pct": 100 * y[high].sum() / pos,
            "brier": float(np.mean((tr.y.mean() - y) ** 2)),
        }
    )
    sizes = {
        "train_mornings": len(tr.y),
        "test_mornings": len(y),
        "test_positive": int(pos),
        "positive_rate_pct": round(100 * float(y.mean()), 2),
        "morse_high_share_pct": round(100 * float(high.mean()), 1),
        "capacity_k": CAPACITY_K,
    }
    return Backtest(rows, sizes)

"""Three claim models, each backtested against the rule Ferrowind uses today.

* **Complexity at first notice** (`complexity_*`): the probability a new claim is complex, from what the
  intake desk records: line, cause, channel, estimate, injury, police report, third-party flag, photos,
  days from loss to report, policy tenure and prior claims. The label is the adjuster's complexity code
  at first assessment (`silver.claim_events`). Compared with the current routing rule.
* **Leakage at payment** (`leakage_*`): the probability that a proposed payment contains an overpayment,
  from the payment request (amount, ratio to the first estimate, invoices, repairer outside the network),
  the queue that handled the claim and first-notice facts. Labels come only from the random closed-file
  audits, the one unbiased record of overpayments. Compared with "review the largest payments".
* **Subrogation at payment** (`subrogation_*`): the probability a third party can be recovered from, also
  learned from the audits. Compared with "refer what the intake desk flagged".

No model sees the claimant's identity, postcode district or proxy group, and none may be used to deny
or reduce a claim (the contracts prohibit claim denial without human review). Days from loss to report
is a feature; it is correlated with the synthetic proxy group, which is why the fairness audit
(adl.domains.insurance.fairness) checks the decisions these scores drive.

All three are L2-regularised logistic regressions (adl.core.logit). Training uses claims or audits up
to a cut-off day and testing uses later ones whose labels were observed by day 179, so no test label is
seen in training.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.core.logit import Logistic, auc
from adl.domains.insurance import world as W

C = {c: i for i, c in enumerate(W.CAUSES)}
COMPLEXITY_FEATURES = (
    "log_estimate", "injury", "commercial", "home", "fire_or_liability", "water_escape", "collision_third_party", "theft", "glass",
    "report_lag_days", "police_report", "third_party_flag", "photos", "phone", "broker", "tenure_years", "prior_claims",
)  # fmt: skip
COMPLEXITY_DRIVERS = {
    0: ("large first estimate", 1), 1: ("injury reported", 1), 2: ("commercial line", 1), 4: ("fire or liability loss", 1),
    5: ("escape of water", 1), 9: ("reported late", 1), 12: ("no photos", -1),
}  # fmt: skip
LEAKAGE_FEATURES = (
    "log_proposed", "proposed_to_estimate", "invoices", "non_network_repairer", "report_lag_days", "prior_claims", "theft_or_water",
    "fast_track", "standard", "escalated", "days_open", "commercial", "home",
)  # fmt: skip
LEAKAGE_DRIVERS = {
    1: ("payment well above the first estimate", 1), 2: ("many invoices", 1), 3: ("repairer outside the network", 1),
    4: ("reported late", 1), 5: ("prior claims", 1), 7: ("handled in fast track", 1),
}  # fmt: skip
SUBROGATION_FEATURES = (
    "collision_third_party", "water_escape", "liability", "fire", "police_report", "third_party_flag", "auto", "log_proposed", "collision_single",
)  # fmt: skip
TRAIN_REPORTED, TEST_REPORTED = range(10, 130), range(140, 166)  # complexity: claims reported in these days
TRAIN_AUDITS, TEST_AUDITS = range(0, 136), range(136, W.DAYS_HISTORY)  # leakage and subrogation: audits on these days
EXPECTED_OVERPAY_SHARE = 0.26  # planning figure: mean overpayment found by audits, as a share of the proposed payment


def _complexity_matrix(line, cause, channel, estimate, injury, police, tp_flag, photos, late, tenure, prior) -> np.ndarray:
    cols = [
        np.log(np.maximum(estimate, 50) / 4000),
        injury,
        line == W.LINES.index("commercial"),
        line == W.LINES.index("home"),
        np.isin(cause, [C["fire"], C["liability"]]),
        cause == C["water_escape"],
        cause == C["collision_third_party"],
        cause == C["theft"],
        cause == C["glass"],
        np.minimum(late, 14),
        police,
        tp_flag,
        photos,
        channel == W.CHANNELS.index("phone"),
        channel == W.CHANNELS.index("broker"),
        np.minimum(tenure, 20),
        prior,
    ]
    return np.column_stack([np.asarray(c, float) for c in cols]) if len(estimate) else np.zeros((0, len(COMPLEXITY_FEATURES)))


def complexity_features(v: W.View) -> np.ndarray:
    p = lambda a: v.part("new", a)  # noqa: E731
    return _complexity_matrix(
        p(v.line), p(v.cause), p(v.channel), p(v.estimate), p(v.injury), p(v.police), p(v.tp_flag), p(v.photos), p(v.late), p(v.tenure), p(v.prior)
    )


def _pay_matrix(proposed, estimate, invoices, nonnetwork, late, prior, cause, queue, escalated, days_open, line) -> np.ndarray:
    cols = [
        np.log(np.maximum(proposed, 50) / 4000),
        np.minimum(proposed / np.maximum(estimate, 50), 10),
        invoices,
        nonnetwork,
        np.minimum(late, 14),
        prior,
        np.isin(cause, [C["theft"], C["water_escape"]]),
        queue == W.FT,
        queue == W.STD,
        escalated,
        np.minimum(days_open, 60),
        line == W.LINES.index("commercial"),
        line == W.LINES.index("home"),
    ]
    return np.column_stack([np.asarray(c, float) for c in cols]) if len(proposed) else np.zeros((0, len(LEAKAGE_FEATURES)))


def _subro_matrix(cause, police, tp_flag, line, proposed) -> np.ndarray:
    cols = [
        cause == C["collision_third_party"],
        cause == C["water_escape"],
        cause == C["liability"],
        cause == C["fire"],
        police,
        tp_flag,
        line == W.LINES.index("auto"),
        np.log(np.maximum(proposed, 50) / 4000),
        cause == C["collision_single"],
    ]
    return np.column_stack([np.asarray(c, float) for c in cols]) if len(proposed) else np.zeros((0, len(SUBROGATION_FEATURES)))


def leakage_features(v: W.View) -> np.ndarray:
    p = lambda a: v.part("pay", a)  # noqa: E731
    return _pay_matrix(
        v.proposed, p(v.estimate), v.invoices, v.nonnetwork, p(v.late), p(v.prior), p(v.cause), v.queue, v.escalated, v.days_open, p(v.line)
    )


def subrogation_features(v: W.View) -> np.ndarray:
    p = lambda a: v.part("pay", a)  # noqa: E731
    return _subro_matrix(p(v.cause), p(v.police), p(v.tp_flag), p(v.line), v.proposed)


def current_rule_queue(estimate: np.ndarray, injury: np.ndarray, line: np.ndarray) -> np.ndarray:
    return np.where((injury == 1) | (line == W.LINES.index("commercial")) | (estimate > 15000), W.CX, np.where(estimate < 3000, W.FT, W.STD))


# ------------------------------------------------------------------ datasets from silver
CLAIM_SQL = """
    SELECT c.claim_id, c.line, c.cause, c.channel, c.estimate_usd, c.injury, c.police_report, c.third_party_flag, c.photos,
           c.report_lag_days, c.reported_day, p.tenure_years, p.prior_claims_3y, e.detail AS code, e.day AS assessed_day
    FROM silver.claims c JOIN silver.policies p USING (policy_id)
    JOIN silver.claim_events e ON e.claim_id = c.claim_id AND e.event = 'assessed'
    WHERE c.reported_day BETWEEN ? AND ? AND e.day <= ? ORDER BY c.claim_id"""
AUDIT_SQL = """
    WITH q AS (SELECT a.claim_id, arg_max(q.queue, q.day) AS queue, bool_or(q.reason = 'escalation') AS escalated
               FROM silver.audits a JOIN silver.payment_requests r USING (claim_id) JOIN silver.queue_assignments q USING (claim_id)
               WHERE q.day <= r.day GROUP BY a.claim_id)
    SELECT a.claim_id, a.day, a.overpayment_found_usd, a.subrogation_potential, r.proposed_usd, r.invoices, r.network_repairer, r.day AS ready_day,
           c.line, c.cause, c.estimate_usd, c.report_lag_days, c.reported_day, c.police_report, c.third_party_flag, p.prior_claims_3y, q.queue, q.escalated
    FROM silver.audits a JOIN silver.payment_requests r USING (claim_id) JOIN silver.claims c USING (claim_id)
    JOIN silver.policies p USING (policy_id) JOIN q USING (claim_id)
    WHERE a.day BETWEEN ? AND ? AND r.proposed_usd >= 0 ORDER BY a.claim_id"""


def _arr(rows, k, dt=float):
    return np.array([r[k] for r in rows], dt)


def complexity_dataset(store, reported: range, cutoff: int) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    rows = store.sql(CLAIM_SQL, [reported.start, reported.stop - 1, cutoff])
    X = _complexity_matrix(
        np.array([W.LINES.index(r["line"]) for r in rows]),
        np.array([C[r["cause"]] for r in rows]),
        np.array([W.CHANNELS.index(r["channel"]) for r in rows]),
        _arr(rows, "estimate_usd"),
        _arr(rows, "injury"),
        _arr(rows, "police_report"),
        _arr(rows, "third_party_flag"),
        _arr(rows, "photos"),
        _arr(rows, "report_lag_days"),
        _arr(rows, "tenure_years"),
        _arr(rows, "prior_claims_3y"),
    )
    return X, np.array([r["code"] == "complex" for r in rows], float), rows


def audit_dataset(store, days: range) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[dict]]:
    rows = store.sql(AUDIT_SQL, [days.start, days.stop - 1])
    cause = np.array([C[r["cause"]] for r in rows])
    line = np.array([W.LINES.index(r["line"]) for r in rows])
    proposed = _arr(rows, "proposed_usd")
    XL = _pay_matrix(
        proposed,
        _arr(rows, "estimate_usd"),
        _arr(rows, "invoices"),
        np.array([0.0 if r["network_repairer"] else 1.0 for r in rows]),
        _arr(rows, "report_lag_days"),
        _arr(rows, "prior_claims_3y"),
        cause,
        np.array([W.QUEUES.index(r["queue"]) for r in rows]),
        _arr(rows, "escalated"),
        _arr(rows, "ready_day") + 1 - _arr(rows, "reported_day"),
        line,
    )
    XS = _subro_matrix(cause, _arr(rows, "police_report"), _arr(rows, "third_party_flag"), line, proposed)
    yl = (_arr(rows, "overpayment_found_usd") > 0).astype(float)
    ys = _arr(rows, "subrogation_potential")
    return XL, yl, XS, ys, rows


# ------------------------------------------------------------------ models and backtests
@dataclass
class Models:
    complexity: Logistic
    leakage: Logistic
    subrogation: Logistic

    def complexity_p(self, v: W.View) -> np.ndarray:
        return self.complexity.predict(complexity_features(v))

    def leakage_p(self, v: W.View) -> np.ndarray:
        return self.leakage.predict(leakage_features(v))

    def subrogation_p(self, v: W.View) -> np.ndarray:
        return self.subrogation.predict(subrogation_features(v))


def fit_production(store) -> Models:
    Xc, yc, _ = complexity_dataset(store, range(10, W.DAYS_HISTORY), W.DAYS_HISTORY - 1)
    XL, yl, XS, ys, _ = audit_dataset(store, range(0, W.DAYS_HISTORY))
    return Models(Logistic.fit(Xc, yc), Logistic.fit(XL, yl), Logistic.fit(XS, ys))


@dataclass
class Backtest:
    complexity: list[dict]
    leakage: list[dict]
    subrogation: list[dict]
    sizes: dict[str, int]


def _top(score: np.ndarray, k: int) -> np.ndarray:
    return np.argsort(-score, kind="mergesort")[:k]


def backtest(store) -> Backtest:
    Xtr, ytr, _ = complexity_dataset(store, TRAIN_REPORTED, TRAIN_REPORTED.stop + 19)
    Xte, yte, rows = complexity_dataset(store, TEST_REPORTED, W.DAYS_HISTORY - 1)
    m = Logistic.fit(Xtr, ytr)
    p = m.predict(Xte)
    est, inj = _arr(rows, "estimate_usd"), _arr(rows, "injury")
    line = np.array([W.LINES.index(r["line"]) for r in rows])
    rule = current_rule_queue(est, inj, line)
    k_cx, k_ft = int((rule == W.CX).sum()), int((rule == W.FT).sum())
    model_cx, model_ft = _top(p, k_cx), _top(-p, k_ft)
    n_cx = yte.sum()
    comp = [
        {
            "method": "complexity model",
            "auc": auc(p, yte),
            "complex_unit_precision_pct": 100 * yte[model_cx].mean(),
            "complex_unit_recall_pct": 100 * yte[model_cx].sum() / n_cx,
            "complex_in_fast_track": int(yte[model_ft].sum()),
            "brier": float(np.mean((p - yte) ** 2)),
        },
        {
            "method": "current routing rule",
            "auc": auc(rule.astype(float), yte),
            "complex_unit_precision_pct": 100 * yte[rule == W.CX].mean(),
            "complex_unit_recall_pct": 100 * yte[rule == W.CX].sum() / n_cx,
            "complex_in_fast_track": int(yte[rule == W.FT].sum()),
            "brier": float(np.mean((ytr.mean() - yte) ** 2)),
        },
    ]
    XLtr, yltr, XStr, ystr, _ = audit_dataset(store, TRAIN_AUDITS)
    XLte, ylte, XSte, yste, arows = audit_dataset(store, TEST_AUDITS)
    over = _arr(arows, "overpayment_found_usd")
    proposed = _arr(arows, "proposed_usd")
    k_rev = round(len(arows) * W.REVIEW_CAPACITY / W.CLAIMS_PER_DAY)  # the share of payments the review team can check
    ml = Logistic.fit(XLtr, yltr)
    pl = ml.predict(XLte)
    leak = []
    for name, score in (("leakage model (p x amount)", pl * proposed), ("largest payment first", proposed)):
        sel = _top(score, k_rev)
        leak.append(
            {
                "method": name,
                "auc": auc(pl if name.startswith("leakage") else proposed, ylte),
                "precision_pct": 100 * ylte[sel].mean(),
                "overpayment_found_pct": 100 * over[sel].sum() / max(over.sum(), 1e-9),
            }
        )
    ms = Logistic.fit(XStr, ystr)
    ps = ms.predict(XSte)
    flag = _arr(arows, "third_party_flag")
    k_ref = int(flag.sum())
    subro = []
    for name, score in (("subrogation model", ps), ("intake third-party flag", flag + proposed / 1e9)):
        sel = _top(score, k_ref)
        subro.append(
            {
                "method": name,
                "auc": auc(ps if name.startswith("subrogation") else flag, yste),
                "precision_pct": 100 * yste[sel].mean(),
                "recall_pct": 100 * yste[sel].sum() / max(yste.sum(), 1),
            }
        )
    sizes = {
        "complexity_test": len(yte),
        "complex_share_pct": round(100 * float(yte.mean()), 1),
        "audits_test": len(ylte),
        "leak_share_pct": round(100 * float(ylte.mean()), 1),
        "recoverable_share_pct": round(100 * float(yste.mean()), 1),
        "review_k": k_rev,
        "referral_k": k_ref,
    }
    return Backtest(comp, leak, subro, sizes)

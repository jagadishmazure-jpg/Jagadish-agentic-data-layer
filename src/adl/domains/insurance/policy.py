"""The assistant's policy, shared by the agent workflow and the forward value simulation.

Each morning, within the capacity the teams have today:

* **Queue assignment:** send a new claim to the complex unit when its complexity probability is high,
  to fast track when it is low and the estimate is modest, otherwise to standard, then move claims back
  to standard if the expected adjuster-days would exceed a queue's capacity (planning figures from
  config/insurance/policy.yaml).
* **Leakage review:** review the payments with the highest expected overpayment caught (probability x
  proposed amount x the audits' mean overpayment share), when that exceeds the cost of a review.
* **Subrogation referral:** refer the payments with the highest expected recovery, when that exceeds the
  cost of a referral.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from adl import ROOT
from adl.domains.insurance import world as W
from adl.domains.insurance.models import EXPECTED_OVERPAY_SHARE, Models

POLICY = ROOT / "config/insurance/policy.yaml"


def load_policy(path: Path = POLICY) -> dict:
    return yaml.safe_load(path.read_text())


def assign_queues(p: np.ndarray, estimate: np.ndarray, ids: np.ndarray, cfg: dict) -> np.ndarray:
    tr, pl, cap = cfg["triage"], cfg["planning"], cfg["capacity"]["adjuster_days"]
    work = np.array([pl["work"][q] for q in W.QUEUES])
    util = pl["utilisation_target"]
    q = np.where(
        p >= tr["complex_min_probability"],
        W.CX,
        np.where((p <= tr["fast_track_max_probability"]) & (estimate <= tr["fast_track_max_estimate_usd"]), W.FT, W.STD),
    )

    def load(k: int) -> np.ndarray:
        return (1 - p) * work[k, 0] + p * work[k, 1]

    ft = np.flatnonzero(q == W.FT)  # keep fast track within capacity: drop the least certain first
    for i in ft[np.lexsort((ids[ft], -p[ft]))]:
        if load(W.FT)[q == W.FT].sum() <= util * cap["fast_track"]:
            break
        q[i] = W.STD
    budget = util * cap["complex"] - (p[q == W.FT] * work[W.CX, 1]).sum()  # escalations from fast track land in the complex unit
    cx = np.flatnonzero(q == W.CX)
    for i in cx[np.lexsort((ids[cx], p[cx]))]:
        if load(W.CX)[q == W.CX].sum() <= budget:
            break
        q[i] = W.STD
    return q


def review_value(p: np.ndarray, proposed: np.ndarray) -> np.ndarray:
    return p * proposed * EXPECTED_OVERPAY_SHARE * W.REVIEW_CATCH - W.REVIEW_COST


def referral_value(p: np.ndarray, proposed: np.ndarray) -> np.ndarray:
    return p * proposed * W.RECOVERY_SHARE * W.RECOVERY_SUCCESS - W.REFERRAL_COST


def choose(value: np.ndarray, ids: np.ndarray, cap: int) -> np.ndarray:
    order = np.lexsort((ids, -value))
    return order[value[order] > 0][:cap]


class AgentPolicy(W.Policy):
    name = "agent"

    def __init__(self, models: Models, cfg: dict, triage: bool = True, review: bool = True, refer: bool = True) -> None:
        self.m, self.cfg = models, cfg
        self.triage, self.review, self.refer = triage, review, refer
        self.base = W.CurrentRules()

    def decide(self, v: W.View) -> W.Decision:
        base = self.base.decide(v)
        cap = self.cfg["capacity"]
        q = base.queue
        if self.triage and len(v.new):
            q = assign_queues(self.m.complexity_p(v), v.part("new", v.estimate), v.new, self.cfg)
        reviews, referrals = base.reviews, base.referrals
        if self.review and len(v.pay):
            reviews = v.pay[choose(review_value(self.m.leakage_p(v), v.proposed), v.pay, cap["reviews_per_day"])]
        if self.refer and len(v.pay):
            referrals = v.pay[choose(referral_value(self.m.subrogation_p(v), v.proposed), v.pay, cap["referrals_per_day"])]
        return W.Decision(q, reviews, referrals)

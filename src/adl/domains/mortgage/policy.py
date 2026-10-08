"""The agents' policy, shared by the agent workflow and the forward value simulation.

Each morning, within the same capacity the teams have today (60 calls, 60 document chases):

* **Borrower outreach:** call the locks with the most value at risk, probability of fallout times
  the gain on sale plus any hedge loss, instead of whoever is closest to expiry.
* **Document chase:** chase the files with outstanding documents that are least likely to finish
  before the lock expires (smallest slack), instead of whoever is closest to expiry.
* **Lock extension:** extend a lock that expires today unless relocking at the market rate leaves
  the borrower no worse off and costs the lender less than the fee (the market has fallen by less
  than the fee is worth). Current practice extends every expiring lock.

Thresholds and capacities come from config/mortgage/policy.yaml.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from adl import ROOT
from adl.domains.mortgage import world as W
from adl.domains.mortgage.risk import FalloutModel, features, value_at_risk

POLICY = ROOT / "config/mortgage/policy.yaml"


def load_policy(path: Path = POLICY) -> dict:
    return yaml.safe_load(path.read_text())


def slack(v: W.View) -> np.ndarray:
    """Days to expiry minus a planning estimate of the days still needed (documents, then processing)."""
    rate = (v.n_cond - v.docs_out) / np.maximum(v.t - v.lock_day, 1)
    est = v.docs_out / np.maximum(rate, 0.15) + (1 - np.array(W.STAGE_AT)[v.stage]) * W.TYPICAL_WORK * 0.6
    return (v.expiry - v.t) - est


def choose_calls(p: np.ndarray, var: np.ndarray, ids: np.ndarray, cap: int) -> np.ndarray:
    return np.lexsort((ids, -(p * var)))[:cap]


def choose_chases(v: W.View, cap: int) -> np.ndarray:
    s = slack(v)
    order = np.lexsort((v.idx, s))
    return np.array([i for i in order if v.docs_out[i] > 0][:cap], int)


def choose_extensions(v: W.View, max_extensions: int) -> np.ndarray:
    gap = v.locked_rate - v.market_rate  # positive: the market is cheaper than the lock
    relock_ok = (gap >= 0) & (gap * W.HEDGE_DURATION / 100 < W.EXTENSION_FEE)
    return np.flatnonzero((v.expiry == v.t) & (v.extensions < max_extensions) & ~relock_ok)


class AgentPolicy(W.Policy):
    name = "agent"

    def __init__(self, model: FalloutModel, cfg: dict, outreach: bool = True, chase: bool = True, extend: bool = True) -> None:
        self.model, self.cfg = model, cfg
        self.outreach, self.chase, self.extend = outreach, chase, extend
        self.base = W.CurrentRules()

    def decide(self, v: W.View) -> W.Decision:
        cap = self.cfg["capacity"]
        base = self.base.decide(v)
        calls = base.calls
        if self.outreach:
            p = self.model.predict(features(v))
            calls = v.idx[choose_calls(p, value_at_risk(v), v.idx, cap["calls_per_day"])]
        chases = v.idx[choose_chases(v, cap["chases_per_day"])] if self.chase else base.chases
        extend = v.idx[choose_extensions(v, self.cfg["extension"]["max_extensions"])] if self.extend else base.extend
        return W.Decision(calls, chases, extend)

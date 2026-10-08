"""The assistant's policy, shared by the agent workflow and the forward value simulation.

Each morning, within the capacity the wards have today, for every patient in hospital:

1. the fall-risk model gives the probability of a fall in the next three days, turned into a daily risk;
2. the nursing signals seen yesterday (confusion, toileting calls, unsteady gait) choose how much of that
   risk each measure is expected to remove (planning figures in config/healthcare/policy.yaml);
3. the expected benefit of a measure is daily risk x expected cost of a fall x that share; a mobility aid
   counts three days of benefit;
4. measures are given in the order sitter, bed alarm, hourly rounding, mobility aid, each to the patients
   with the highest benefit up to its capacity, and each later measure works on the risk left after the
   earlier ones. Bed alarms and the rounding rota are already staffed (`fill_capacity`), so they go to
   the patients who gain the most; a sitter shift or a mobility aid is requested only when the benefit
   exceeds its cost.

The policy can only choose among the four nursing measures. It has no way to express a medication,
a diagnosis, a treatment or a discharge, and every measure it proposes waits for a nurse.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from adl import ROOT
from adl.domains.healthcare import world as W
from adl.domains.healthcare.models import FallModel
from adl.domains.healthcare.pipeline import pseudonym

POLICY = ROOT / "config/healthcare/policy.yaml"
ORDER = ("sitter", "bed_alarm", "hourly_rounding", "mobility_aid")


_KEYS: dict[int, str] = {}


def keys(idx: np.ndarray) -> np.ndarray:
    """Pseudonymous keys for hospital indices: the tie-break order the agents (who see only keys) also use."""
    for i in idx:
        if int(i) not in _KEYS:
            _KEYS[int(i)] = pseudonym(f"ENC-{int(i) + 1:06d}")
    return np.array([_KEYS[int(i)] for i in idx])


def load_policy(path: Path = POLICY) -> dict:
    return yaml.safe_load(path.read_text())


def signals(confusion, restless, calls, unsteady, morse_gait, morse_mental) -> dict[str, np.ndarray]:
    sig = {
        "confusion": (np.asarray(confusion) == 1) | (np.asarray(restless) == 1) | (np.asarray(morse_mental) == 15),
        "toileting": np.asarray(calls) >= 3,
        "gait": (np.asarray(unsteady) == 1) | (np.asarray(morse_gait) == 20),
    }
    sig["none"] = ~(sig["confusion"] | sig["toileting"] | sig["gait"])
    return sig


def effect(cfg: dict, measure: str, sig: dict[str, np.ndarray]) -> np.ndarray:
    table = cfg["planning"]["effect"][measure]
    return np.max([np.where(sig[k], table[k], 0.0) for k in ("confusion", "toileting", "gait", "none")], axis=0)


def expected_fall_cost(cfg: dict, harm_share: float) -> float:
    pl = cfg["planning"]
    return (1 - harm_share) * pl["fall_cost_usd"] + harm_share * pl["harm_fall_cost_usd"]


def plan(p3: np.ndarray, sig: dict[str, np.ndarray], has_aid: np.ndarray, ids: np.ndarray, cfg: dict, harm_share: float, use=ORDER) -> dict:
    """Positions (into the arrays) chosen for each measure, and each chosen measure's expected benefit net of its cost, USD."""
    daily = 1 - (1 - np.clip(p3 * cfg.get("risk_multiplier", 1.0), 0, 0.999)) ** (1 / 3)
    cost_fall = expected_fall_cost(cfg, harm_share)
    pl, cap = cfg["planning"], cfg["capacity"]
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    left = daily.copy()
    for m in ORDER:
        if m not in use:
            out[m] = (np.zeros(0, int), np.zeros(0))
            continue
        e = effect(cfg, m, sig)
        days = pl["aid_horizon_days"] if m == "mobility_aid" else 1
        gross = left * cost_fall * e * days
        value = gross if m in cfg.get("fill_capacity", ()) else gross - pl["measure_cost_usd"][m]
        if m == "mobility_aid":
            value = np.where(np.asarray(has_aid) == 1, -np.inf, value)
        order = np.lexsort((ids, -value))
        chosen = order[value[order] > 0][: cap[m]]
        out[m] = (chosen, gross[chosen] - pl["measure_cost_usd"][m])
        left[chosen] = left[chosen] * (1 - e[chosen])
    return out


class AgentPolicy(W.Policy):
    """The assistant's measures for one morning. `use` switches measures on one at a time (the others follow
    the current rules), which attributes the value to each measure."""

    name = "agent"

    def __init__(self, model: FallModel, cfg: dict, harm_share: float, use: tuple[str, ...] = ORDER) -> None:
        self.m, self.cfg, self.harm_share, self.use = model, cfg, harm_share, use
        self.base = W.CurrentRules()

    def decide(self, v: W.View) -> W.Decision:
        base = self.base.decide(v)
        if len(v.idx) == 0:
            return base
        sig = signals(v.obs[:, 0], v.obs[:, 1], v.obs[:, 2], v.obs[:, 3], v.morse[:, 4], v.morse[:, 5])
        chosen = plan(self.m.p(v), sig, v.has_aid, keys(v.idx), self.cfg, self.harm_share, self.use)
        pick = {m: v.idx[chosen[m][0]] if m in self.use else getattr(base, m) for m in ORDER}
        return W.Decision(pick["bed_alarm"], pick["hourly_rounding"], pick["sitter"], pick["mobility_aid"])

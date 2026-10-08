"""Build the healthcare lake and everything on top of it, once per process. Synthetic, PHI-free data.

`lake()` runs the chain: synthetic history -> bronze -> silver -> gold -> fall-risk model (fitted on every
labelled patient-morning) -> gold.fall_risk_worklist for the morning of day 180 -> knowledge index ->
gateway. `valued()` adds the forward simulation and writes gold.value_ledger. Both are cached, so the
CLI and the tests share one build.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from functools import cache

import pyarrow as pa

from adl import ROOT
from adl.core import agentflow
from adl.core.access import DataGateway, load_identities
from adl.core.audit import AuditLog
from adl.core.contracts import load_all
from adl.core.lineage import Lineage
from adl.core.semantic import SemanticLayer
from adl.domains.healthcare import agents as A
from adl.domains.healthcare import fairness as FA
from adl.domains.healthcare import models as M
from adl.domains.healthcare import pipeline as P
from adl.domains.healthcare import policy as PO
from adl.domains.healthcare import simulate as SIM
from adl.domains.healthcare import synth
from adl.domains.healthcare import value as V
from adl.domains.healthcare import world as W
from adl.domains.healthcare.knowledge import build_index
from adl.storage.local import LocalDeltaStore

CONTRACTS = ROOT / "domains/healthcare/contracts"
INJECTED_GROUPS = ["W02", "W10", "W12"]  # the wards whose notes carry an injection attempt (NN-005, NN-013, NN-021)


@dataclass
class Lake:
    history: synth.History
    build: P.Build
    model: M.FallModel
    policy: dict
    semantic: SemanticLayer
    index: object
    gateway: DataGateway
    audit: AuditLog

    @property
    def store(self) -> LocalDeltaStore:
        return self.build.store

    def policies(self) -> dict[str, W.Policy]:
        m, c, hs = self.model, self.policy, self.model.harm_share
        return {
            "current rules": W.CurrentRules(),
            "agent": PO.AgentPolicy(m, c, hs),
            "bed alarm only": PO.AgentPolicy(m, c, hs, ("bed_alarm",)),
            "rounding only": PO.AgentPolicy(m, c, hs, ("hourly_rounding",)),
            "mobility aid only": PO.AgentPolicy(m, c, hs, ("mobility_aid",)),
            "sitter only": PO.AgentPolicy(m, c, hs, ("sitter",)),
            "agent, sitters as today": PO.AgentPolicy(m, c, hs, ("bed_alarm", "hourly_rounding", "mobility_aid")),
        }


def new_gateway(store, contracts, semantic, index, audit: AuditLog | None = None) -> DataGateway:
    ids, kpol = load_identities(ROOT / "config/healthcare/agents.yaml")
    return DataGateway(store, contracts, ids, semantic, audit or AuditLog("healthcare"), index, kpol, scope_table=("wards", "ward_id"))


def worklist_table(store, model: M.FallModel) -> pa.Table:
    o = P.observe(store, P.AS_OF + 1)
    v = o.view
    p = model.p(v)
    drivers = model.drivers(v)
    conf = {1: "yes", 0: "no", -1: "not documented"}
    rows = []
    for i, r in enumerate(o.rows):
        yday = [k for k, on in (("bed_alarm", v.alarm[i]), ("hourly_rounding", v.rounding[i]), ("sitter", v.sitter[i])) if on]
        rows.append(
            {
                "encounter_key": P.pseudonym(r["encounter_id"]),
                "ward_id": r["ward_id"],
                "region": r["region"],
                "bed": r["bed"],
                "age_band": r["age_band"],
                "days_in_hospital": int(v.days_in[i]),
                "morse_total": int(v.morse_total[i]),
                "morse_high_risk": bool(v.morse_total[i] >= 45),
                "morse_gait_impaired": bool(v.morse[i, 4] == 20),
                "morse_forgets_limits": bool(v.morse[i, 5] == 15),
                "confusion": conf[int(v.obs[i, 0])],
                "night_restless": bool(v.obs[i, 1]),
                "toileting_calls": int(v.obs[i, 2]),
                "unsteady_gait": bool(v.obs[i, 3]),
                "has_mobility_aid": bool(v.has_aid[i]),
                "measures_yesterday": ", ".join(yday) or "none",
                "fall_risk_3d": float(p[i]),
                "risk_band": M.band(p[i : i + 1])[0],
                "top_driver": drivers[i],
                "as_of_day": P.AS_OF,
            }
        )
    return pa.Table.from_pylist(rows)


@cache
def lake(root: str | None = None) -> Lake:
    h = synth.simulate_history()
    store = LocalDeltaStore(root or tempfile.mkdtemp(prefix="adl-healthcare-"))
    b = P.Build(store, load_all(CONTRACTS), Lineage(dataset_namespace="lake://local/healthcare"))
    P.land_bronze(b, synth.bronze_tables(h))
    P.build_silver(b)
    P.build_gold(b)
    m = M.fit_production(store)
    P.write_gold(b, "fall_risk_worklist", worklist_table(store, m), ["model.fall_risk_3d"])
    sem = SemanticLayer.load(ROOT / "domains/healthcare/metrics.yaml")
    idx = build_index(store)
    audit = AuditLog("healthcare")
    gw = new_gateway(store, b.contracts, sem, idx, audit)
    return Lake(h, b, m, PO.load_policy(), sem, idx, gw, audit)


@cache
def valued(seeds: tuple[int, ...] = SIM.EVAL_SEEDS) -> tuple[Lake, V.Ledger]:
    lk = lake()
    cmp = SIM.compare(lk.history.world, lk.history.state, lk.policies(), seeds)
    led = V.ledger(cmp)
    if "healthcare.gold.value_ledger" not in lk.build.quality:
        P.write_gold(lk.build, "value_ledger", pa.Table.from_pylist(led.rows), ["model.value_simulation"])
    return lk, led


@cache
def backtest() -> M.Backtest:
    return M.backtest(lake().store)


@cache
def retrieval_eval() -> dict:
    from adl.domains.healthcare.knowledge import load_questions
    from adl.knowledge.retrieve import evaluate

    return evaluate(lake().index, load_questions())


def fresh_gateway() -> DataGateway:
    lk = lake()
    return new_gateway(lk.store, lk.build.contracts, lk.semantic, lk.index)


@cache
def agent_run():
    lk = lake()
    gw = fresh_gateway()
    plans = A.plan_measures(gw, lk.model.harm_share, lk.policy)
    cases = asyncio.run(agentflow.run_all(A.deps(gw, plans), list(W.WARDS), A.nurse_in_charge))
    return gw, plans, cases


@cache
def injection_eval() -> list[dict]:
    lk = lake()

    def make(guard: bool, validate: bool):
        gw = fresh_gateway()
        return A.deps(gw, A.plan_measures(gw, lk.model.harm_share, lk.policy), guard=guard, validate=validate, gullible=True)

    return agentflow.injection_matrix(make, INJECTED_GROUPS, A.nurse_in_charge)


@cache
def fairness() -> tuple[list[dict], list[dict]]:
    lk, led = valued()
    return FA.history(lk.store), FA.forward(led.comparison)


def policy_decision_today() -> W.Decision:
    """The policy's decision for the morning of day 180 from the silver view, for checking the agents' plan."""
    lk = lake()
    v = P.observe(lk.store, P.AS_OF + 1).view
    return PO.AgentPolicy(lk.model, lk.policy, lk.model.harm_share).decide(v)


__all__ = ["Lake", "agent_run", "backtest", "fairness", "fresh_gateway", "injection_eval", "lake", "retrieval_eval", "valued"]

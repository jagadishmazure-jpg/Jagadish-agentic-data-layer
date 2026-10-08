"""Build the insurance lake and everything on top of it, once per process.

`lake()` runs the chain: synthetic history -> bronze -> silver -> gold -> claim models (fitted on every
labelled claim and audit) -> gold.claims_triage and gold.leakage_signals for the morning of day 180 ->
knowledge index -> gateway. `valued()` adds the forward simulation and writes gold.value_ledger. Both
are cached, so the CLI and the tests share one build.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from functools import cache

import numpy as np
import pyarrow as pa

from adl import ROOT
from adl.core import agentflow
from adl.core.access import DataGateway, load_identities
from adl.core.audit import AuditLog
from adl.core.contracts import load_all
from adl.core.lineage import Lineage
from adl.core.semantic import SemanticLayer
from adl.domains.insurance import agents as A
from adl.domains.insurance import fairness as FA
from adl.domains.insurance import models as M
from adl.domains.insurance import pipeline as P
from adl.domains.insurance import policy as PO
from adl.domains.insurance import simulate as SIM
from adl.domains.insurance import synth
from adl.domains.insurance import value as V
from adl.domains.insurance import world as W
from adl.domains.insurance.knowledge import build_index
from adl.storage.local import LocalDeltaStore

CONTRACTS = ROOT / "domains/insurance/contracts"
INJECTED_GROUPS = ["F02", "F04", "F06"]  # the offices whose notes carry an injection attempt (CN-005, CN-013, CN-021)


@dataclass
class Lake:
    history: synth.History
    build: P.Build
    models: M.Models
    policy: dict
    semantic: SemanticLayer
    index: object
    gateway: DataGateway
    audit: AuditLog

    @property
    def store(self) -> LocalDeltaStore:
        return self.build.store

    def policies(self) -> dict[str, W.Policy]:
        m, c = self.models, self.policy
        return {
            "current rules": W.CurrentRules(),
            "agent": PO.AgentPolicy(m, c),
            "triage only": PO.AgentPolicy(m, c, review=False, refer=False),
            "review only": PO.AgentPolicy(m, c, triage=False, refer=False),
            "referral only": PO.AgentPolicy(m, c, triage=False, review=False),
        }


def new_gateway(store, contracts, semantic, index, audit: AuditLog | None = None) -> DataGateway:
    ids, kpol = load_identities(ROOT / "config/insurance/agents.yaml")
    return DataGateway(store, contracts, ids, semantic, audit or AuditLog("insurance"), index, kpol, scope_table=("offices", "office_id"))


def claims_triage_table(store, models: M.Models, cfg: dict) -> pa.Table:
    o = P.observe(store, P.AS_OF + 1)
    v = o.view
    X = M.complexity_features(v)
    p = models.complexity.predict(X)
    drivers = models.complexity.top_driver(X, M.COMPLEXITY_DRIVERS)
    est, inj, line = v.part("new", v.estimate), v.part("new", v.injury), v.part("new", v.line)
    rule = M.current_rule_queue(est, inj, line)
    q = PO.assign_queues(p, est, v.new, cfg)
    return pa.Table.from_pylist(
        [
            {
                "claim_id": r["claim_id"],
                "office_id": r["office_id"],
                "region": r["region"],
                "line": r["line"],
                "cause": r["cause"],
                "channel": r["channel"],
                "estimate_usd": float(r["estimate_usd"]),
                "complexity_probability": round(float(p[i]), 4),
                "top_driver": drivers[i],
                "current_rule_queue": W.QUEUES[rule[i]],
                "suggested_queue": W.QUEUES[q[i]],
                "as_of_day": P.AS_OF,
            }
            for i, r in enumerate(o.new_rows)
        ]
    )


def leakage_signals_table(store, models: M.Models) -> pa.Table:
    o = P.observe(store, P.AS_OF + 1)
    v = o.view
    XL = M.leakage_features(v)
    pl = models.leakage.predict(XL)
    ps = models.subrogation_p(v)
    drivers = models.leakage.top_driver(XL, M.LEAKAGE_DRIVERS)
    return pa.Table.from_pylist(
        [
            {
                "claim_id": r["claim_id"],
                "office_id": r["office_id"],
                "region": r["region"],
                "line": r["line"],
                "cause": r["cause"],
                "proposed_usd": float(v.proposed[i]),
                "leakage_probability": round(float(pl[i]), 4),
                "expected_overpayment_usd": round(float(pl[i] * v.proposed[i] * M.EXPECTED_OVERPAY_SHARE), 2),
                "leakage_driver": drivers[i],
                "subrogation_probability": round(float(ps[i]), 4),
                "expected_recovery_usd": round(float(ps[i] * v.proposed[i] * W.RECOVERY_SHARE * W.RECOVERY_SUCCESS), 2),
                "as_of_day": P.AS_OF,
            }
            for i, r in enumerate(o.pay_rows)
        ]
    )


@cache
def lake(root: str | None = None) -> Lake:
    h = synth.simulate_history()
    store = LocalDeltaStore(root or tempfile.mkdtemp(prefix="adl-insurance-"))
    b = P.Build(store, load_all(CONTRACTS), Lineage(dataset_namespace="lake://local/insurance"))
    P.land_bronze(b, synth.bronze_tables(h))
    P.build_silver(b)
    P.build_gold(b)
    m = M.fit_production(store)
    cfg = PO.load_policy()
    P.write_gold(b, "claims_triage", claims_triage_table(store, m, cfg), ["model.claim_complexity"])
    P.write_gold(b, "leakage_signals", leakage_signals_table(store, m), ["model.claim_leakage", "model.claim_subrogation"])
    sem = SemanticLayer.load(ROOT / "domains/insurance/metrics.yaml")
    idx = build_index(store)
    audit = AuditLog("insurance")
    gw = new_gateway(store, b.contracts, sem, idx, audit)
    return Lake(h, b, m, cfg, sem, idx, gw, audit)


@cache
def valued(seeds: tuple[int, ...] = SIM.EVAL_SEEDS) -> tuple[Lake, V.Ledger]:
    lk = lake()
    cmp = SIM.compare(lk.history.world, lk.history.state, lk.policies(), seeds)
    led = V.ledger(cmp)
    if "insurance.gold.value_ledger" not in lk.build.quality:
        P.write_gold(lk.build, "value_ledger", pa.Table.from_pylist(led.rows), ["model.value_simulation"])
    return lk, led


@cache
def backtest() -> M.Backtest:
    return M.backtest(lake().store)


@cache
def retrieval_eval() -> dict:
    from adl.domains.insurance.knowledge import load_questions
    from adl.knowledge.retrieve import evaluate

    return evaluate(lake().index, load_questions())


def fresh_gateway() -> DataGateway:
    lk = lake()
    return new_gateway(lk.store, lk.build.contracts, lk.semantic, lk.index)


@cache
def agent_run():
    lk = lake()
    gw = fresh_gateway()
    plans = A.plan_claims(gw, lk.policy)
    cases = asyncio.run(agentflow.run_all(A.deps(gw, plans), list(W.OFFICES), A.team_lead))
    return gw, plans, cases


@cache
def injection_eval() -> list[dict]:
    lk = lake()

    def make(guard: bool, validate: bool):
        gw = fresh_gateway()
        return A.deps(gw, A.plan_claims(gw, lk.policy), guard=guard, validate=validate, gullible=True)

    return agentflow.injection_matrix(make, INJECTED_GROUPS, A.team_lead)


@cache
def fairness() -> tuple[list[dict], list[dict]]:
    lk, led = valued()
    return FA.history(lk.store), FA.forward(led.comparison)


def policy_decision_today() -> W.Decision:
    """The policy's decision for the morning of day 180 from the silver view, for checking the agents' plan."""
    lk = lake()
    v = P.observe(lk.store, P.AS_OF + 1).view
    return PO.AgentPolicy(lk.models, lk.policy).decide(v)


__all__ = ["Lake", "agent_run", "backtest", "fairness", "fresh_gateway", "injection_eval", "lake", "np", "retrieval_eval", "valued"]

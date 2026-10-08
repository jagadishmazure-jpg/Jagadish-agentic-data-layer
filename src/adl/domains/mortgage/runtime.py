"""Build the mortgage lake and everything on top of it, once per process.

`lake()` runs the chain: synthetic history -> bronze -> silver -> gold -> fallout model (fitted on
every origin with an observed outcome) -> gold.fallout_risk -> knowledge index -> gateway.
`valued()` adds the forward simulation and writes gold.value_ledger. Both are cached, so the CLI and
the tests share one build.
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
from adl.domains.mortgage import agents as A
from adl.domains.mortgage import pipeline as P
from adl.domains.mortgage import policy as PO
from adl.domains.mortgage import risk as R
from adl.domains.mortgage import simulate as SIM
from adl.domains.mortgage import synth
from adl.domains.mortgage import value as V
from adl.domains.mortgage import world as W
from adl.domains.mortgage.knowledge import build_index
from adl.storage.local import LocalDeltaStore

CONTRACTS = ROOT / "domains/mortgage/contracts"
INJECTED_GROUPS = ["LO03", "LO07", "LO10"]  # the loan officers whose notes carry an injection attempt


@dataclass
class Lake:
    history: synth.History
    build: P.Build
    model: R.FalloutModel
    policy: dict
    semantic: SemanticLayer
    index: object
    gateway: DataGateway
    audit: AuditLog

    @property
    def store(self) -> LocalDeltaStore:
        return self.build.store

    def policies(self) -> dict[str, W.Policy]:
        m, c = self.model, self.policy
        return {
            "current rules": W.CurrentRules(),
            "agent": PO.AgentPolicy(m, c),
            "outreach only": PO.AgentPolicy(m, c, chase=False, extend=False),
            "document chase only": PO.AgentPolicy(m, c, outreach=False, extend=False),
            "extension only": PO.AgentPolicy(m, c, outreach=False, chase=False),
        }


def new_gateway(store, contracts, semantic, index, audit: AuditLog | None = None) -> DataGateway:
    ids, kpol = load_identities(ROOT / "config/mortgage/agents.yaml")
    return DataGateway(store, contracts, ids, semantic, audit or AuditLog("mortgage"), index, kpol, scope_table=("branches", "branch_id"))


def fallout_risk_table(store, model: R.FalloutModel) -> pa.Table:
    o = P.observe(store, P.AS_OF + 1)
    v = o.view
    X = R.features(v)
    p = model.predict(X)
    var = R.value_at_risk(v)
    drivers = model.drivers(X)
    bands = R.band(p)
    return pa.Table.from_pylist(
        [
            {
                "application_id": r["application_id"],
                "branch_id": r["branch_id"],
                "region": r["region"],
                "lo_id": r["lo_id"],
                "product": r["product"],
                "channel": r["channel"],
                "days_to_lock_expiry": int(v.expiry[i] - v.t),
                "rate_gap_bps": round(float((v.locked_rate[i] - v.market_rate) * 100), 1),
                "probability": round(float(p[i]), 4),
                "risk_band": bands[i],
                "top_driver": drivers[i],
                "value_at_risk_usd": round(float(var[i]), 2),
            }
            for i, r in enumerate(o.rows)
        ]
    )


@cache
def lake(root: str | None = None) -> Lake:
    h = synth.simulate_history()
    store = LocalDeltaStore(root or tempfile.mkdtemp(prefix="adl-mortgage-"))
    b = P.Build(store, load_all(CONTRACTS), Lineage(dataset_namespace="lake://local/mortgage"))
    P.land_bronze(b, synth.bronze_tables(h))
    P.build_silver(b)
    P.build_gold(b)
    m = R.fit_production(store)
    P.write_gold(b, "fallout_risk", fallout_risk_table(store, m), ["model.fallout_risk"])
    sem = SemanticLayer.load(ROOT / "domains/mortgage/metrics.yaml")
    idx = build_index(store)
    audit = AuditLog("mortgage")
    gw = new_gateway(store, b.contracts, sem, idx, audit)
    return Lake(h, b, m, PO.load_policy(), sem, idx, gw, audit)


@cache
def valued(seeds: tuple[int, ...] = SIM.EVAL_SEEDS) -> tuple[Lake, V.Ledger]:
    lk = lake()
    cmp = SIM.compare(lk.history.world, lk.history.state, lk.policies(), seeds)
    led = V.ledger(cmp)
    if "mortgage.gold.value_ledger" not in lk.build.quality:
        P.write_gold(lk.build, "value_ledger", pa.Table.from_pylist(led.rows), ["model.value_simulation"])
    return lk, led


@cache
def risk_backtest() -> R.Backtest:
    return R.backtest(lake().store)


@cache
def retrieval_eval() -> dict:
    from adl.domains.mortgage.knowledge import load_questions
    from adl.knowledge.retrieve import evaluate

    return evaluate(lake().index, load_questions())


def fresh_gateway() -> DataGateway:
    lk = lake()
    return new_gateway(lk.store, lk.build.contracts, lk.semantic, lk.index)


@cache
def agent_run():
    lk = lake()
    gw = fresh_gateway()
    plans = A.plan_pipeline(gw, lk.policy)
    cases = asyncio.run(agentflow.run_all(A.deps(gw, plans), sorted(W.LOAN_OFFICERS), A.pipeline_manager))
    return gw, plans, cases


@cache
def injection_eval() -> list[dict]:
    lk = lake()

    def make(guard: bool, validate: bool):
        gw = fresh_gateway()
        return A.deps(gw, A.plan_pipeline(gw, lk.policy), guard=guard, validate=validate, gullible=True)

    return agentflow.injection_matrix(make, INJECTED_GROUPS, A.pipeline_manager)

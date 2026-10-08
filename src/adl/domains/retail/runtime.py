"""Build the retail lake and everything on top of it, once per process.

`lake()` runs the whole chain in memory-backed temp storage: synthetic history -> bronze -> silver ->
gold -> features -> forecaster (trained through day 139) -> insight products -> knowledge index ->
gateway. `valued()` adds the forward simulation and writes gold.value_ledger. Both are cached, so the
CLI, the tests, the MCP server and the A2A endpoint share one build.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from functools import cache

import pyarrow as pa

from adl import ROOT
from adl.core.access import DataGateway, load_identities
from adl.core.audit import AuditLog
from adl.core.contracts import load_all
from adl.core.lineage import Lineage
from adl.core.semantic import SemanticLayer
from adl.domains.retail import features, insights, synth
from adl.domains.retail import markdown as MD
from adl.domains.retail import pipeline as P
from adl.domains.retail import policy as PO
from adl.domains.retail import simulate as SIM
from adl.domains.retail import value as V
from adl.domains.retail import world as W
from adl.domains.retail.forecast import Forecaster
from adl.domains.retail.knowledge import build_index
from adl.storage.local import LocalDeltaStore

CONTRACTS = ROOT / "domains/retail/contracts"


@dataclass
class Lake:
    history: synth.History
    build: P.Build
    frame: features.Frame
    model: Forecaster
    elasticity: dict[str, float]
    tests: list
    take30: dict[str, float]
    policy: dict
    insight: dict
    semantic: SemanticLayer
    index: object
    gateway: DataGateway
    audit: AuditLog
    extras: dict = field(default_factory=dict)

    @property
    def store(self) -> LocalDeltaStore:
        return self.build.store

    def policies(self) -> dict[str, object]:
        h, f, m, e, t = self.history, self.frame, self.model, self.elasticity, self.take30
        return {
            "current rules": W.LegacyPolicy(),
            "agent": PO.AgentPolicy(h.world, f, m, e, t, self.policy),
            "replenishment only": PO.AgentPolicy(h.world, f, m, e, t, self.policy, mark_down=False, transfer=False),
            "markdown only": PO.AgentPolicy(h.world, f, m, e, t, self.policy, replenish=False, transfer=False),
        }


def new_gateway(lake_store, contracts, semantic, index, audit: AuditLog | None = None) -> DataGateway:
    ids, kpol = load_identities(ROOT / "config/agents.yaml")
    return DataGateway(lake_store, contracts, ids, semantic, audit or AuditLog("retail"), index, kpol)


@cache
def lake(root: str | None = None) -> Lake:
    h = synth.simulate_history()
    store = LocalDeltaStore(root or tempfile.mkdtemp(prefix="adl-lake-"))
    b = P.Build(store, load_all(CONTRACTS), Lineage())
    P.land_bronze(b, synth.bronze_tables(h))
    P.build_silver(b)
    P.build_gold(b)
    f = features.build_frame(store)
    m = Forecaster.fit(f, P.AS_OF)
    el, tests = MD.elasticity_from_tests(f, store.sql("SELECT * FROM silver.price_tests"))
    t30 = MD.take30(f)
    pol = PO.load_policy()
    ins = insights.build(b, f, m, el, t30, pol)
    sem = SemanticLayer.load(ROOT / "domains/retail/metrics.yaml")
    idx = build_index(store)
    audit = AuditLog("retail")
    gw = new_gateway(store, b.contracts, sem, idx, audit)
    return Lake(h, b, f, m, el, tests, t30, pol, ins, sem, idx, gw, audit)


@cache
def valued(seeds: tuple[int, ...] = SIM.EVAL_SEEDS) -> tuple[Lake, V.Ledger]:
    lk = lake()
    cmp = SIM.compare(lk.history.world, lk.history.calendar, lk.history.state, lk.policies(), seeds)
    led = V.ledger(cmp)
    if "retail.gold.value_ledger" not in lk.build.quality:
        P.write_gold(lk.build, "value_ledger", pa.Table.from_pylist(led.rows), ["model.value_simulation"])
    return lk, led

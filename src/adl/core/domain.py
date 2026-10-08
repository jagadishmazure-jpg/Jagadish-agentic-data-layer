"""Domain registry: what each business domain provides, and whether it is built or planned.

A domain is a folder of contracts and metrics under `domains/<name>/` plus a Python package under
`adl.domains.<name>`. The core (contracts, quality, lineage, semantic layer, gateway, audit, guardrails,
knowledge, storage adapters) is shared; a domain adds its sources, transformations, models and agents.
docs/adding-a-domain.md walks through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from adl import ROOT
from adl.core.contracts import Contract, load_all


@dataclass(frozen=True)
class Domain:
    name: str
    status: str  # built | planned
    organisation: str  # fictional
    use_case: str
    decision: str
    kpis: tuple[str, ...]
    levers: tuple[str, ...]

    @property
    def folder(self) -> Path:
        return ROOT / "domains" / self.name

    def contracts(self) -> dict[str, Contract]:
        return load_all(self.folder / "contracts")


REGISTRY = {
    "retail": Domain(
        "retail",
        "built",
        "Wrenfield Grocers",
        "stockouts and markdown",
        "what to order, move and mark down tonight, per store and product",
        ("stockout rate", "lost sales", "markdown dollars", "waste", "gross margin"),
        ("purchase orders", "inter-store transfers", "markdowns"),
    ),
    "mortgage": Domain(
        "mortgage",
        "built",
        "Quillmere Home Loans",
        "loan pipeline and fallout",
        "which locked applications need a call before the lock expires",
        ("pull-through rate", "fallout rate", "lock extension cost", "cycle time"),
        ("borrower outreach", "lock extension", "document chase"),
    ),
    "insurance": Domain(
        "insurance",
        "planned",
        "Ferrowind Insurance",
        "claims triage and leakage",
        "which queue each new claim goes to, and which closed claims to review for leakage",
        ("cycle time", "leakage dollars", "reopen rate", "adjuster workload"),
        ("queue assignment", "leakage review", "subrogation referral"),
    ),
    "healthcare": Domain(
        "healthcare",
        "planned",
        "Halsey Vale Health",
        "inpatient fall risk",
        "which patients the nurse in charge should check first on each shift",
        ("falls per 1,000 bed-days", "falls with harm", "time to intervention"),
        ("bed alarm", "hourly rounding", "mobility support"),
    ),
}


def get(name: str) -> Domain:
    if name not in REGISTRY:
        raise KeyError(f"unknown domain {name!r}; known: {', '.join(REGISTRY)}")
    return REGISTRY[name]

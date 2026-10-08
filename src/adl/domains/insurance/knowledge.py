"""Insurance knowledge: graph entities from the lake's master data, documents from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from adl import ROOT
from adl.knowledge.graph import Entity, KnowledgeGraph
from adl.knowledge.retrieve import Doc, KnowledgeIndex

KNOWLEDGE = ROOT / "domains/insurance/knowledge"
LINE_ALIASES = {
    "auto": ("motor", "glass", "collision", "driver"),
    "home": ("home", "contents", "escape of water"),
    "commercial": ("commercial", "shops"),
}
CHANNEL_ALIASES = {"phone": ("phone", "by phone"), "broker": ("broker", "brokers"), "app": ("app", "portal")}
TOPICS = {
    "triage": ("queue", "route", "routing", "triage", "complex unit", "standard queue"),
    "fast track": ("fast track", "fast-track"),
    "leakage": ("leakage", "overpayment", "overpayments", "invoice", "invoices", "review team"),
    "subrogation": ("subrogation", "recovered", "recovery", "recoveries", "third party"),
    "audit": ("audit", "audits"),
    "approval": ("approval", "approve", "approves", "approved"),
    "fair claims": ("postcode", "deny", "protected characteristic", "reduce a payment"),
    "privacy": ("phone numbers", "contact details", "e-mail addresses"),
    "reopen": ("reopen", "reopened"),
}


def load_docs(path: Path | None = None) -> list[Doc]:
    spec = yaml.safe_load((path or KNOWLEDGE / "corpus.yaml").read_text())
    return [Doc(d["id"], d["type"], d["title"], d["text"], tuple(d.get("regions", ()))) for d in spec["docs"]]


def load_questions(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or KNOWLEDGE / "eval.yaml").read_text())["questions"]


def build_graph(store) -> KnowledgeGraph:
    g = KnowledgeGraph()
    for r in store.sql("SELECT DISTINCT region FROM silver.offices ORDER BY region"):
        g.add_entity(Entity(f"region:{r['region']}", "region", r["region"], ()))
    for r in store.sql("SELECT office_id, name, region FROM silver.offices ORDER BY office_id"):
        short = r["name"].replace("Ferrowind ", "")
        g.add_entity(Entity(f"office:{r['office_id']}", "office", r["name"], (short.lower(),)))
        g.relate(f"office:{r['office_id']}", f"region:{r['region']}")
    for line, aliases in LINE_ALIASES.items():
        g.add_entity(Entity(f"line:{line}", "line", f"{line} claims", aliases))
    for c, aliases in CHANNEL_ALIASES.items():
        g.add_entity(Entity(f"channel:{c}", "channel", f"{c} channel", aliases))
    for t, aliases in TOPICS.items():
        g.add_entity(Entity(f"topic:{t}", "topic", t, aliases))
    g.relate("topic:fast track", "topic:triage")
    g.relate("topic:leakage", "topic:audit")
    g.relate("topic:triage", "topic:reopen")
    g.relate("channel:phone", "topic:fair claims")
    return g


def build_index(store) -> KnowledgeIndex:
    return KnowledgeIndex(load_docs(), build_graph(store))

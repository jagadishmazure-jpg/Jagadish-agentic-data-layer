"""Mortgage knowledge: graph entities from the lake's master data, documents from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from adl import ROOT
from adl.knowledge.graph import Entity, KnowledgeGraph
from adl.knowledge.retrieve import Doc, KnowledgeIndex

KNOWLEDGE = ROOT / "domains/mortgage/knowledge"
PRODUCT_ALIASES = {
    "conv30": ("30-year", "30-year conventional", "conventional"),
    "conv15": ("15-year",),
    "fha": ("fha", "government loans"),
    "va": ("va", "government loans"),
    "jumbo": ("jumbo",),
}
CHANNEL_ALIASES = {"broker": ("brokers", "broker"), "direct": ("online", "website", "direct online"), "retail": ("retail",)}
TOPICS = {
    "extension": ("extension", "extensions", "extend", "extending"),
    "relock": ("relock", "relocking"),
    "outreach": ("call", "calls", "outreach"),
    "documents": ("documents", "document", "conditions", "appraisal", "insurance binder", "explanation letters"),
    "approval": ("approval", "approve", "approves", "approved"),
    "fair lending": ("decline", "credit decisions", "protected characteristic"),
    "privacy": ("phone numbers", "contact details", "e-mail addresses"),
    "rate drop": ("rate rally", "rates fall", "rates drop", "rate drops", "market rates fell"),
}


def load_docs(path: Path | None = None) -> list[Doc]:
    spec = yaml.safe_load((path or KNOWLEDGE / "corpus.yaml").read_text())
    return [Doc(d["id"], d["type"], d["title"], d["text"], tuple(d.get("regions", ()))) for d in spec["docs"]]


def load_questions(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or KNOWLEDGE / "eval.yaml").read_text())["questions"]


def build_graph(store) -> KnowledgeGraph:
    g = KnowledgeGraph()
    for r in store.sql("SELECT DISTINCT region FROM silver.branches ORDER BY region"):
        g.add_entity(Entity(f"region:{r['region']}", "region", r["region"], ()))
    for r in store.sql("SELECT branch_id, name, region FROM silver.branches ORDER BY branch_id"):
        short = r["name"].replace("Quillmere ", "")
        g.add_entity(Entity(f"branch:{r['branch_id']}", "branch", r["name"], (short.lower(),)))
        g.relate(f"branch:{r['branch_id']}", f"region:{r['region']}")
    for r in store.sql("SELECT product FROM silver.products ORDER BY product"):
        g.add_entity(Entity(f"product:{r['product']}", "product", r["product"], PRODUCT_ALIASES.get(r["product"], ())))
    for c, aliases in CHANNEL_ALIASES.items():
        g.add_entity(Entity(f"channel:{c}", "channel", f"{c} channel", aliases))
    for t, aliases in TOPICS.items():
        g.add_entity(Entity(f"topic:{t}", "topic", t, aliases))
    g.relate("topic:extension", "topic:relock")
    g.relate("topic:extension", "topic:approval")
    g.relate("product:jumbo", "topic:extension")
    g.relate("channel:broker", "topic:rate drop")
    return g


def build_index(store) -> KnowledgeIndex:
    return KnowledgeIndex(load_docs(), build_graph(store))

"""Healthcare knowledge: graph entities from the lake's master data, documents from YAML. Synthetic, no patient data."""

from __future__ import annotations

from pathlib import Path

import yaml

from adl import ROOT
from adl.domains.healthcare import world as W
from adl.knowledge.graph import Entity, KnowledgeGraph
from adl.knowledge.retrieve import Doc, KnowledgeIndex

KNOWLEDGE = ROOT / "domains/healthcare/knowledge"
TOPICS = {
    "morse": ("morse", "morse scale", "assessment", "high risk"),
    "bed alarm": ("bed alarm", "bed alarms", "alarm", "sensor mat"),
    "rounding": ("rounding", "hourly rounding", "rota"),
    "mobility aid": ("walking frame", "frame", "frames", "stick", "mobility aid", "unsteady"),
    "sitter": ("sitter", "sitters", "one-to-one", "sitter request"),
    "approval": ("approve", "approves", "approval", "nurse in charge"),
    "scope": ("medication", "medication change", "diagnosis", "medical device", "discharge"),
    "privacy": ("record numbers", "patient names", "performance management", "minimum necessary"),
    "equity": ("fairness", "documentation", "equity"),
    "delirium": ("delirium", "confusion", "confused", "restless"),
    "toileting": ("toilet", "toileting", "catheter"),
    "sedation": ("sedation", "sedating", "sedation flag"),
    "harm": ("fall with harm", "harm", "fracture", "incident"),
}


def load_docs(path: Path | None = None) -> list[Doc]:
    spec = yaml.safe_load((path or KNOWLEDGE / "corpus.yaml").read_text())
    return [Doc(d["id"], d["type"], d["title"], d["text"], tuple(d.get("regions", ()))) for d in spec["docs"]]


def load_questions(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or KNOWLEDGE / "eval.yaml").read_text())["questions"]


def build_graph(store) -> KnowledgeGraph:
    g = KnowledgeGraph()
    for r in store.sql("SELECT DISTINCT region FROM silver.wards ORDER BY region"):
        g.add_entity(Entity(f"region:{r['region']}", "region", r["region"], (W.SITES[r["region"]].lower(),)))
    for r in store.sql("SELECT ward_id, name, region FROM silver.wards ORDER BY ward_id"):
        g.add_entity(Entity(f"ward:{r['ward_id']}", "ward", r["name"], (r["name"].replace(" ward", "").lower(),)))
        g.relate(f"ward:{r['ward_id']}", f"region:{r['region']}")
    for t, aliases in TOPICS.items():
        g.add_entity(Entity(f"topic:{t}", "topic", t, aliases))
    g.relate("topic:delirium", "topic:bed alarm")
    g.relate("topic:toileting", "topic:rounding")
    g.relate("topic:sedation", "topic:scope")
    g.relate("topic:sitter", "topic:approval")
    return g


def build_index(store) -> KnowledgeIndex:
    return KnowledgeIndex(load_docs(), build_graph(store))

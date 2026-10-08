"""Retail knowledge: the graph's entities come from the lake's master data, the documents from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from adl import ROOT
from adl.knowledge.graph import Entity, KnowledgeGraph
from adl.knowledge.retrieve import Doc, KnowledgeIndex

KNOWLEDGE = ROOT / "domains/retail/knowledge"
PRODUCT_ALIASES = {
    "SKU-PR03": ("spinach",), "SKU-PR04": ("tomatoes",), "SKU-PR05": ("strawberries", "strawberry"), "SKU-PR08": ("romaine", "salads", "salad"),
    "SKU-PR01": ("apples",), "SKU-PR06": ("avocados",), "SKU-DA01": ("milk",), "SKU-DA02": ("yoghurt",), "SKU-DA05": ("oat drink",),
    "SKU-DA06": ("eggs",), "SKU-BA01": ("sourdough",), "SKU-BA03": ("croissants", "croissant"), "SKU-BA05": ("cinnamon buns",),
    "SKU-BA08": ("muffins",), "SKU-ME01": ("chicken",), "SKU-ME03": ("sausages",), "SKU-ME04": ("salmon",), "SKU-FR02": ("pizza",),
    "SKU-FR03": ("ice cream",), "SKU-FR06": ("frozen berries",), "SKU-PA01": ("rice",), "SKU-PA02": ("pasta",), "SKU-PA03": ("tinned tomatoes",),
}  # fmt: skip
CATEGORY_ALIASES = {
    "bakery": ("bread", "loaf"),
    "meat": ("fish", "meat and fish"),
    "produce": ("fruit", "vegetables"),
    "frozen": (),
    "pantry": (),
    "dairy": (),
}


def load_docs(path: Path | None = None) -> list[Doc]:
    spec = yaml.safe_load((path or KNOWLEDGE / "corpus.yaml").read_text())
    return [Doc(d["id"], d["type"], d["title"], d["text"], tuple(d.get("regions", ()))) for d in spec["docs"]]


def load_questions(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or KNOWLEDGE / "eval.yaml").read_text())["questions"]


def build_graph(store) -> KnowledgeGraph:
    g = KnowledgeGraph()
    for r in store.sql("SELECT DISTINCT region FROM silver.stores"):
        g.add_entity(Entity(f"region:{r['region']}", "region", r["region"], ()))
    for r in store.sql("SELECT store_id, name, region FROM silver.stores ORDER BY store_id"):
        short = r["name"].replace("Wrenfield ", "")
        g.add_entity(Entity(f"store:{r['store_id']}", "store", r["name"], (short.lower(), r["store_id"].lower())))
        g.relate(f"store:{r['store_id']}", f"region:{r['region']}")
    for c, extra in CATEGORY_ALIASES.items():
        g.add_entity(Entity(f"category:{c}", "category", c, extra))
    for r in store.sql("SELECT supplier_id, name, categories FROM silver.suppliers ORDER BY supplier_id"):
        first = r["name"].split()[0].lower()
        g.add_entity(Entity(f"supplier:{r['supplier_id']}", "supplier", r["name"], (first,)))
        for c in r["categories"].split(","):
            g.relate(f"supplier:{r['supplier_id']}", f"category:{c}")
    for r in store.sql("SELECT sku, name, category, supplier_id FROM silver.products ORDER BY sku"):
        g.add_entity(Entity(f"product:{r['sku']}", "product", r["name"], PRODUCT_ALIASES.get(r["sku"], ())))
        g.relate(f"product:{r['sku']}", f"category:{r['category']}")
        g.relate(f"product:{r['sku']}", f"supplier:{r['supplier_id']}")
    return g


def build_index(store) -> KnowledgeIndex:
    return KnowledgeIndex(load_docs(), build_graph(store))

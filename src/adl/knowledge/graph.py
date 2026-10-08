"""Knowledge graph: business entities, their relationships, and which documents mention them.

Nodes are entities from the gold and silver master data (stores, regions, products, categories,
suppliers) plus documents. Edges come from the data (a product belongs to a category and is supplied
by a supplier; a store is in a region) and from entity linking (a document mentions an entity). The
same alias matcher links questions to entities, which is what lets retrieval follow a relationship the
question never states, such as spinach -> Copperleaf Produce -> late deliveries.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Entity:
    id: str
    kind: str
    name: str
    aliases: tuple[str, ...]


@dataclass
class KnowledgeGraph:
    entities: dict[str, Entity] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    mentions: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # doc id -> entity ids
    _patterns: list[tuple[re.Pattern, str]] = field(default_factory=list)

    def add_entity(self, e: Entity) -> None:
        self.entities[e.id] = e
        for a in {e.name.lower(), *e.aliases}:
            self._patterns.append((re.compile(rf"\b{re.escape(a)}\b", re.I), e.id))
        self._patterns.sort(key=lambda p: -len(p[0].pattern))

    def relate(self, a: str, b: str) -> None:
        self.edges[a].add(b)
        self.edges[b].add(a)

    def link(self, text: str) -> set[str]:
        return {eid for pat, eid in self._patterns if pat.search(text)}

    def add_document(self, doc_id: str, text: str) -> None:
        self.mentions[doc_id] = self.link(text)

    def neighbours(self, entity_id: str) -> set[str]:
        return set(self.edges.get(entity_id, set()))

    def stats(self) -> dict[str, int]:
        kinds: dict[str, int] = defaultdict(int)
        for e in self.entities.values():
            kinds[e.kind] += 1
        return {
            **{f"{k} nodes": v for k, v in sorted(kinds.items())},
            "document nodes": len(self.mentions),
            "entity-entity edges": sum(len(v) for v in self.edges.values()) // 2,
            "document-entity edges": sum(len(v) for v in self.mentions.values()),
        }

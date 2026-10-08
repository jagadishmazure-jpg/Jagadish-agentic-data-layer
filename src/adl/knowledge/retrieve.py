"""Retrieval: vector search, graph search (GraphRAG local search) and a hybrid of the two.

* Vector: cosine similarity between the question and each document (hashed TF-IDF embeddings).
* Graph: link the question to entities, add their one-hop neighbours at half weight, and score each
  document by the weight of the entities it mentions, divided by how many entities it mentions (so a
  policy that names everything does not win every question). Ties break on vector similarity.
* Hybrid: reciprocal rank fusion (k = 60) of the two rankings; with no linked entity it is the vector
  ranking.

Every result is security-trimmed by the caller's regions before ranking, and document text is
screened for prompt injection and returned quoted, never as instructions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adl.core import guardrails
from adl.knowledge.embed import HashedTfidf
from adl.knowledge.graph import KnowledgeGraph

RRF_K = 60
METHODS = ("vector", "graph", "hybrid")


@dataclass(frozen=True)
class Doc:
    id: str
    type: str
    title: str
    text: str
    regions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hit:
    doc_id: str
    title: str
    score: float
    text: str
    injection_flag: bool
    via: str


class KnowledgeIndex:
    def __init__(self, docs: list[Doc], graph: KnowledgeGraph) -> None:
        self.docs = {d.id: d for d in docs}
        self.ids = [d.id for d in docs]
        self.embed = HashedTfidf([f"{d.title}. {d.text}" for d in docs])
        self.matrix = np.vstack([self.embed(f"{d.title}. {d.text}") for d in docs])
        self.graph = graph
        for d in docs:
            graph.add_document(d.id, f"{d.title}. {d.text}")

    def _allowed(self, regions: tuple[str, ...] | None) -> np.ndarray:
        if regions is None:
            return np.ones(len(self.ids), bool)
        return np.array([not self.docs[i].regions or bool(set(self.docs[i].regions) & set(regions)) for i in self.ids])

    def vector_scores(self, q: str) -> np.ndarray:
        return self.matrix @ self.embed(q)

    def rank(self, q: str, method: str = "hybrid", regions: tuple[str, ...] | None = None) -> list[tuple[str, float]]:
        allowed = self._allowed(regions)
        vec = self.vector_scores(q)
        vec_rank = [(self.ids[i], float(vec[i])) for i in np.argsort(-vec, kind="stable") if allowed[i]]
        if method == "vector":
            return vec_rank
        seeds = self.graph.link(q)
        weight: dict[str, float] = {}
        for s in seeds:
            weight[s] = 1.0
            for nb in self.graph.neighbours(s):
                weight[nb] = max(weight.get(nb, 0.0), 0.5)
        g = []
        for i, doc_id in enumerate(self.ids):
            ents = self.graph.mentions.get(doc_id, set())
            sc = sum(weight.get(e, 0.0) for e in ents) / (1 + 0.15 * len(ents))
            if allowed[i] and sc > 0:
                g.append((doc_id, sc + 1e-3 * vec[i]))
        g.sort(key=lambda x: -x[1])
        if method == "graph":
            return g
        if not g:
            return vec_rank
        fused: dict[str, float] = {}
        for ranking in (vec_rank, g):
            for r, (doc_id, _) in enumerate(ranking, 1):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (RRF_K + r)
        return sorted(fused.items(), key=lambda x: (-x[1], self.ids.index(x[0])))

    def search(self, q: str, k: int = 5, method: str = "hybrid", regions: tuple[str, ...] | None = None) -> list[Hit]:
        hits = []
        for doc_id, score in self.rank(q, method, regions)[:k]:
            d = self.docs[doc_id]
            flagged = guardrails.screen(d.text).flagged
            hits.append(Hit(doc_id, d.title, round(score, 4), guardrails.quote_untrusted(d.text), flagged, method))
        return hits


def recall_at(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant)


def mrr(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    for i, d in enumerate(ranked[:k], 1):
        if d in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at(ranked: list[str], relevant: set[str], k: int = 5) -> float:
    dcg = sum(1.0 / np.log2(i + 1) for i, d in enumerate(ranked[:k], 1) if d in relevant)
    ideal = sum(1.0 / np.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return float(dcg / ideal)


def evaluate(index: KnowledgeIndex, questions: list[dict]) -> dict[str, dict[str, float]]:
    out = {}
    for m in METHODS:
        rows = []
        for q in questions:
            ranked = [d for d, _ in index.rank(q["q"], m)]
            rel = set(q["relevant"])
            rows.append(
                (recall_at(ranked, rel, 3), recall_at(ranked, rel, 5), mrr(ranked, rel), ndcg_at(ranked, rel, 5), float(bool(set(ranked[:5]) & rel)))
            )
        a = np.array(rows)
        out[m] = {"recall@3": a[:, 0].mean(), "recall@5": a[:, 1].mean(), "mrr@10": a[:, 2].mean(), "ndcg@5": a[:, 3].mean(), "hit@5": a[:, 4].mean()}
    return out

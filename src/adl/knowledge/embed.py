"""Deterministic offline embeddings: hashed TF-IDF over words and word pairs.

A stand-in for a hosted embedding model so the index, the retrieval evaluation and the tests run
offline and give the same numbers on every machine. Words are lower-cased, stop words dropped and a
plural "s" stripped; unigrams and bigrams are hashed (BLAKE2b) into 2,048 signed buckets, weighted by
1 + log(tf) times IDF from the corpus, and L2-normalised. On Azure the same interface is served by an
embedding deployment in Foundry and the vectors live in Azure AI Search.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from itertools import pairwise

import numpy as np

DIM = 2048
STOP = set(
    "a an the of to in on for and or is are be by with at as it its this that our we do does should can will what why how who which "
    "when there their from into than more most less not no any all per about only much many has have had was were been".split()
)


def tokens(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]
    return words + [f"{a}_{b}" for a, b in pairwise(words)]


def _bucket(tok: str) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
    return h % DIM, 1.0 if (h >> 63) & 1 else -1.0


class HashedTfidf:
    def __init__(self, corpus: list[str]) -> None:
        df = Counter(t for doc in corpus for t in set(tokens(doc)))
        n = len(corpus)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.default_idf = math.log(1 + n) + 1

    def __call__(self, text: str) -> np.ndarray:
        v = np.zeros(DIM)
        for t, c in Counter(tokens(text)).items():
            i, sign = _bucket(t)
            v[i] += sign * (1 + math.log(c)) * self.idf.get(t, self.default_idf)
        norm = np.linalg.norm(v)
        return v / norm if norm else v

"""Domain-neutral L2-regularised logistic regression (Newton's method on standardised features) and AUC.

Used by the insurance models. Retail and mortgage keep their own model code unchanged; this module is the
shared version a new domain should reuse instead of copying one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Logistic:
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 30) -> Logistic:
        mean, scale = X.mean(0), X.std(0) + 1e-9
        Z = np.column_stack([np.ones(len(X)), (X - mean) / scale])
        w = np.zeros(Z.shape[1])
        reg = np.full(Z.shape[1], l2)
        reg[0] = 0.0
        for _ in range(iters):
            p = 1 / (1 + np.exp(-Z @ w))
            g = Z.T @ (p - y) + reg * w
            H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg)
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        return cls(mean, scale, w[1:], float(w[0]))

    def predict(self, X: np.ndarray) -> np.ndarray:
        if len(X) == 0:
            return np.zeros(0)
        return 1 / (1 + np.exp(-(self.intercept + ((X - self.mean) / self.scale) @ self.coef)))

    def top_driver(self, X: np.ndarray, labels: dict[int, tuple[str, int]]) -> list[str]:
        """Label of the feature that raises each row's score the most (coefficient x standardised value),
        named only when the value sits on the side of the average its label describes."""
        if len(X) == 0:
            return []
        z = (X - self.mean) / self.scale
        keep = sorted(labels)
        side = np.array([labels[j][1] for j in keep])
        contrib = (z * self.coef)[:, keep]
        contrib = np.where((np.sign(z[:, keep]) == side) & (contrib > 0), contrib, -np.inf)
        best = np.argmax(contrib, 1)
        return [labels[keep[b]][0] if np.isfinite(contrib[i, b]) else "no single driver" for i, b in enumerate(best)]


def auc(score: np.ndarray, y: np.ndarray) -> float:
    """Area under the ROC curve with average ranks for ties (Mann-Whitney)."""
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score))
    s = score[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

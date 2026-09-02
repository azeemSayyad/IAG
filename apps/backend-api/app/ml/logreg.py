"""Tiny pure-Python logistic regression (no numpy/sklearn).

Trainable via batch gradient descent; serializes to a plain dict so models can be
stored as JSON. Used for the capacity engine's conversion/reply/book/show
propensity models.
"""

from __future__ import annotations

import math
from typing import List, Optional


def sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


class LogReg:
    def __init__(self, w: Optional[List[float]] = None, b: float = 0.0):
        self.w: List[float] = list(w) if w else []
        self.b: float = float(b)

    def predict_proba(self, x: List[float]) -> Optional[float]:
        if not self.w or len(self.w) != len(x):
            return None
        z = self.b + sum(wi * xi for wi, xi in zip(self.w, x))
        return sigmoid(z)

    def fit(self, X: List[List[float]], y: List[float], epochs: int = 400,
            lr: float = 0.1, l2: float = 1e-3) -> "LogReg":
        if not X:
            return self
        n = len(X[0])
        self.w = [0.0] * n
        self.b = 0.0
        m = len(X)
        for _ in range(epochs):
            for xi, yi in zip(X, y):
                p = self.predict_proba(xi) or 0.0
                err = p - yi
                for j in range(n):
                    self.w[j] -= lr * (err * xi[j] + l2 * self.w[j]) / m
                self.b -= lr * err / m
        return self

    def to_dict(self) -> dict:
        return {"w": self.w, "b": self.b, "n": len(self.w)}

    @classmethod
    def from_dict(cls, d: dict) -> "LogReg":
        return cls(w=d.get("w"), b=d.get("b", 0.0))

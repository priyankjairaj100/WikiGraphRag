"""Hybrid dense + lexical retrieval over a fixed claim corpus.

Dense cosine (MiniLM) and BM25 scores are each min-max normalised to ``[0, 1]`` and combined
``alpha * dense + (1 - alpha) * lexical``. ``search`` returns candidates ranked by the blended
score; lifecycle filtering is applied on top of this (see :func:`lifecycle_rerank`).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..core.claim import Claim
from .bm25 import BM25
from .embed import Embedder


def _minmax(x: np.ndarray) -> np.ndarray:
    lo, hi = float(x.min()), float(x.max())
    if hi - lo < 1e-12:
        return np.zeros_like(x)
    return (x - lo) / (hi - lo)


class HybridRetriever:
    def __init__(
        self,
        claims: Sequence[Claim],
        embedder: Optional[Embedder] = None,
        alpha: float = 0.5,
    ) -> None:
        self.claims: List[Claim] = list(claims)
        self.cids = [c.cid for c in self.claims]
        self.texts = [c.text for c in self.claims]
        self.alpha = alpha
        self.embedder = embedder or Embedder()
        self._emb = self.embedder.encode(self.texts)  # (N, d), normalised
        self._bm25 = BM25(self.texts)

    def scores(self, query: str) -> np.ndarray:
        qv = self.embedder.encode([query])[0]
        dense = self._emb @ qv
        lex = self._bm25.scores(query)
        return self.alpha * _minmax(dense) + (1.0 - self.alpha) * _minmax(lex)

    def dense_scores(self, query: str) -> np.ndarray:
        """Return raw dense cosine scores for temporal retrieval methods."""
        qv = self.embedder.encode([query])[0]
        return (self._emb @ qv).astype(np.float64)

    def search(self, query: str, k: int) -> List[Tuple[str, float]]:
        s = self.scores(query)
        order = np.argsort(-s)[:k]
        return [(self.cids[i], float(s[i])) for i in order]

    def ranked_cids(self, query: str) -> List[str]:
        s = self.scores(query)
        return [self.cids[i] for i in np.argsort(-s)]

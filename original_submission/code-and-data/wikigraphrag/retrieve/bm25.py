"""Okapi BM25 lexical scoring over claim texts (the lexical half of hybrid retrieval)."""

from __future__ import annotations

import re
from typing import List, Sequence

import numpy as np

_TOK = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOK.findall(text.lower())


class BM25:
    def __init__(self, corpus_texts: Sequence[str]) -> None:
        from rank_bm25 import BM25Okapi  # local import

        self._tokenized = [_tokenize(t) for t in corpus_texts]
        self._bm25 = BM25Okapi(self._tokenized)

    def scores(self, query: str) -> np.ndarray:
        return np.asarray(self._bm25.get_scores(_tokenize(query)), dtype=np.float32)

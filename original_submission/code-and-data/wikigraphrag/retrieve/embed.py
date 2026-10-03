"""Sentence embeddings (all-MiniLM-L6-v2), cached on disk by text hash.

Uses ``sentence-transformers`` exactly as the paper does. Embeddings are L2-normalised so a
dot product is cosine similarity. A tiny on-disk cache keeps repeated runs deterministic and
fast (and lets experiments replay without recomputing).
"""

from __future__ import annotations

import hashlib
import os
from typing import List, Optional, Sequence

import numpy as np

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CACHE = os.path.join(ROOT, "data", "cache", "embeddings")


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: Optional[str] = None) -> None:
        self.model_name = model_name
        self._device = device
        self._model = None  # lazy: importing torch is expensive
        self._mem: dict[str, np.ndarray] = {}

    def _ensure(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # local import

            self._model = SentenceTransformer(self.model_name, device=self._device)

    def _key(self, text: str) -> str:
        h = hashlib.sha256((self.model_name + "\x00" + text).encode("utf-8")).hexdigest()
        return h

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        out: List[Optional[np.ndarray]] = [None] * len(texts)
        todo_idx: List[int] = []
        todo_txt: List[str] = []
        for i, t in enumerate(texts):
            k = self._key(t)
            if k in self._mem:
                out[i] = self._mem[k]
                continue
            path = os.path.join(CACHE, k + ".npy")
            if os.path.exists(path):
                v = np.load(path)
                self._mem[k] = v
                out[i] = v
            else:
                todo_idx.append(i)
                todo_txt.append(t)
        if todo_txt:
            self._ensure()
            vecs = self._model.encode(
                todo_txt, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
            ).astype(np.float32)
            os.makedirs(CACHE, exist_ok=True)
            for j, i in enumerate(todo_idx):
                v = vecs[j]
                out[i] = v
                k = self._key(texts[i])
                self._mem[k] = v
                np.save(os.path.join(CACHE, k + ".npy"), v)
        return np.vstack(out)

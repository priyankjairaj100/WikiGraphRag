"""OpenAI chat reader/judge with the same interface and on-disk cache as :class:`QwenReader`.

Used to reproduce the closed-model rows (``gpt-4o-mini`` / ``gpt-4.1-mini``) for the
LLM-judge baseline (Table 2) and the current-answer QA money plot (Table 6). The API key is
read from the ``OPENAI_API_KEY`` environment variable and is never written to disk; only the
model *outputs* are cached (``data/cache/openai_generations.jsonl``), so once generated the
numbers replay with no key -- the paper's "all LLM calls are cached" property.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Dict, List, Optional, Sequence

from ..core.claim import Claim
from .base import build_prompt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CACHE_PATH = os.path.join(ROOT, "data", "cache", "openai_generations.jsonl")


class OpenAIReader:
    """Drop-in reader backed by the OpenAI chat completions API (T=0, deterministic)."""

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        max_new_tokens: int = 32,
        cache_path: str = CACHE_PATH,
    ) -> None:
        self.model_name = model_name
        self.max_new_tokens = max_new_tokens
        self.cache_path = cache_path
        self._client = None
        self._cache: Dict[str, str] = {}
        self._load_cache()

    # -- cache --------------------------------------------------------------
    def _load_cache(self) -> None:
        if os.path.exists(self.cache_path):
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        self._cache[rec["key"]] = rec["out"]

    def _key(self, prompt: str, max_new_tokens: int) -> str:
        blob = f"{self.model_name}\x00{max_new_tokens}\x00{prompt}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _store(self, key: str, out: str) -> None:
        self._cache[key] = out
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "out": out}, ensure_ascii=False) + "\n")

    # -- client -------------------------------------------------------------
    def _ensure(self) -> None:
        if self._client is not None:
            return
        from openai import OpenAI

        # OpenAI() reads OPENAI_API_KEY from the environment; the key is never persisted.
        self._client = OpenAI()

    # -- generation ---------------------------------------------------------
    def generate(self, prompt: str, max_new_tokens: Optional[int] = None) -> str:
        mnt = max_new_tokens or self.max_new_tokens
        key = self._key(prompt, mnt)
        if key in self._cache:
            return self._cache[key]
        self._ensure()
        last_err: Optional[Exception] = None
        for attempt in range(5):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=mnt,
                )
                ans = (resp.choices[0].message.content or "").strip()
                self._store(key, ans)
                return ans
            except Exception as exc:  # transient rate limit / network -> backoff
                last_err = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"OpenAI call failed after retries: {last_err}")

    def answer(self, query: str, contexts: Sequence[Claim]) -> str:
        return self.generate(build_prompt(query, contexts), self.max_new_tokens)

    def batch(self, queries: Sequence[str], contexts: Sequence[Sequence[Claim]]) -> List[str]:
        return [self.answer(q, cs) for q, cs in zip(queries, contexts)]

"""Readers: turn a query + retrieved claims into an answer.

Two readers share one interface:

* :class:`ExtractiveReader` -- deterministic, no model. It returns the value of the
  top-ranked retrieved claim, which makes the *which-value* effect of Lemma 1 measurable
    without any LLM: with flat retrieval the top claim may be stale, with lifecycle filtering it
    is the current one. Ideal for a fast, reproducible end-to-end signal.
* :class:`QwenReader` (see ``qwen.py``) -- the real local LLM reader used for the headline
  numbers, added once the model is available.
"""

from __future__ import annotations

from typing import List, Protocol, Sequence

from ..core.claim import Claim


def build_prompt(query: str, contexts: Sequence[Claim]) -> str:
    """A minimal date-stamped RAG prompt (shared by the LLM readers)."""
    lines = [
        "Answer the question using ONLY the evidence. If several values conflict, prefer the",
        "one that is currently true. Answer with just the value.",
        "",
        "Evidence:",
    ]
    for c in contexts:
        stamp = ""
        if c.meta and c.meta.get("start"):
            stamp = f" (dated {c.meta['start'][:10]})"
        elif c.timestamp:
            stamp = f" (dated {int(c.timestamp)})"
        lines.append(f"- {c.text}{stamp}")
    lines += ["", f"Question: {query}", "Answer:"]
    return "\n".join(lines)


def build_prompt_dated(query: str, contexts: Sequence[Claim]) -> str:
    """Date-aware prompt baseline: explicitly instruct the reader to trust the newest date.

    This is the reader-side counterpart to date-reranking -- it changes which value the reader
    *prefers*, but not which values it *sees* (the paper's distinction).
    """
    lines = [
        "Answer the question using ONLY the evidence. Each item is dated; when values conflict,",
        "TRUST THE ITEM WITH THE MOST RECENT DATE and ignore older ones. Answer with just the value.",
        "",
        "Evidence:",
    ]
    for c in contexts:
        stamp = ""
        if c.meta and c.meta.get("start"):
            stamp = f" (dated {c.meta['start'][:10]})"
        elif c.timestamp:
            stamp = f" (dated {int(c.timestamp)})"
        lines.append(f"- {c.text}{stamp}")
    lines += ["", f"Question: {query}", "Answer:"]
    return "\n".join(lines)


def build_prompt_neutral(query: str, contexts: Sequence[Claim]) -> str:
    """Neutral RAG prompt with no recency instruction -- the honest no-signal flat baseline."""
    lines = ["Answer the question using ONLY the evidence. Answer with just the value.", "",
             "Evidence:"]
    for c in contexts:
        stamp = ""
        if c.meta and c.meta.get("start"):
            stamp = f" (dated {c.meta['start'][:10]})"
        elif c.timestamp:
            stamp = f" (dated {int(c.timestamp)})"
        lines.append(f"- {c.text}{stamp}")
    lines += ["", f"Question: {query}", "Answer:"]
    return "\n".join(lines)


class Reader(Protocol):
    def answer(self, query: str, contexts: Sequence[Claim]) -> str: ...


class ExtractiveReader:
    """Return the value of the first (top-ranked) retrieved claim -- no model, deterministic."""

    def answer(self, query: str, contexts: Sequence[Claim]) -> str:
        if not contexts:
            return ""
        top = contexts[0]
        return top.value if top.value else top.text

    def batch(self, queries: Sequence[str], contexts: Sequence[Sequence[Claim]]) -> List[str]:
        return [self.answer(q, cs) for q, cs in zip(queries, contexts)]

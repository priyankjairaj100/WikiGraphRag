"""End-task QA scoring: exact match (EM) of the normalized gold value in the reader's answer.

Follows the paper's definition -- EM counts a hit when the normalized gold value appears in
the reader's answer, so a reader that says "The current CEO is Tim Cook." scores against gold
"Tim Cook". Numbers are compared by unit-normalised magnitude so "£15,000" matches "15000".
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..parse.text import numeric_magnitude

_ARTICLES = {"a", "an", "the"}
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s%]")


def normalize_answer(text: str) -> str:
    text = text.lower()
    text = _PUNCT.sub(" ", text)
    tokens = [t for t in text.split() if t not in _ARTICLES]
    return _WS.sub(" ", " ".join(tokens)).strip()


def exact_match(answer: str, gold_value: str) -> bool:
    """True iff the (normalized) gold value is present in the (normalized) answer."""
    if answer is None:
        return False
    mag = numeric_magnitude(gold_value)
    if mag is not None:
        # numeric gold: match any number in the answer with the same magnitude
        for m in re.finditer(r"[£$€]?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|percent|million|billion|k|m|bn)?",
                             answer, re.IGNORECASE):
            am = numeric_magnitude(m.group(0))
            if am is not None and abs(am - mag) < 1e-6:
                return True
        return False
    gold = normalize_answer(gold_value)
    return bool(gold) and gold in normalize_answer(answer)


def em_score(answers: List[str], gold_values: List[str]) -> float:
    if not answers:
        return 0.0
    hits = sum(1 for a, g in zip(answers, gold_values) if exact_match(a, g))
    return hits / len(answers)


def clean_match(answer: str, gold_value: str, stale_values: List[str]) -> bool:
    """Stricter EM: the answer contains the gold value and NONE of the competing stale values.

    The lenient :func:`exact_match` fires whenever the gold value appears anywhere, so a reader
    that hedges (``the CEO was X, now Y``) still scores a hit against gold ``Y``. ``clean_match``
    additionally requires that no superseded value of the same key is present, so it measures
    whether the reader returned the current value \\emph{cleanly}.
    """
    if not exact_match(answer, gold_value):
        return False
    g = normalize_answer(gold_value)
    return not any(exact_match(answer, s) for s in stale_values if normalize_answer(s) != g)


def best_effort_answer_presence(context: str, gold_value: str) -> Optional[bool]:
    """Whether the gold value is even present in the retrieved context (recall ceiling)."""
    return exact_match(context, gold_value)

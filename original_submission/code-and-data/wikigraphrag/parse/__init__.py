"""Parsing primitives used by the detector: spans, FRAME, VALUES, PHRASES.

These are intentionally dependency-free (pure regex + Python) so the detector is
deterministic, fast, and runnable before any heavy ML install. They implement the
token split described in Section 3: each claim's tokens divide into a *frame* (relation
words, e.g. "head of government of"), *value objects* (numbers or proper-noun spans), and
*phrases* (the capitalized spans that name subjects, e.g. "United Kingdom").
"""

from .text import (
    sentences,
    proper_noun_phrases,
    proper_noun_count,
    numbers,
    numeric_magnitude,
    frame_tokens,
    value_objects,
    value_content_tokens,
    content_tokens,
    overlap_coefficient,
    is_bare_year,
    normalize_phrase,
)

__all__ = [
    "sentences",
    "proper_noun_phrases",
    "proper_noun_count",
    "numbers",
    "numeric_magnitude",
    "frame_tokens",
    "value_objects",
    "value_content_tokens",
    "content_tokens",
    "overlap_coefficient",
    "is_bare_year",
    "normalize_phrase",
]

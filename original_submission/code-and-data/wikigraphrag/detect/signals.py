"""The three detector signals: subject+relation, changed value, recency.

Each is a pure predicate over the pre-computed :class:`ClaimFeatures`, mirroring Section 3.
Thresholds are constructor-style module constants so the empirical loop can tune them against
the rebuilt benchmarks without touching the logic.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from ..core.claim import Claim
from ..parse.text import overlap_coefficient
from .features import ClaimFeatures

# Section 3: "frames overlap by an overlap coefficient of at least 0.5".
FRAME_OVERLAP_THRESHOLD = 0.5
# Fallback subject test when a claim names <= 1 proper noun ("strong token overlap").
TOKEN_OVERLAP_THRESHOLD = 0.5

# Lexical recency cues that raise edge confidence 0.7 -> 0.9 (not required).
_CUE = re.compile(
    r"\b(revis(?:e|ed|ion)|replac(?:e|es|ed)|supersed(?:e|es|ed)|"
    r"with effect from|effective|as of|updated|amend(?:ed|ment)|"
    r"renamed|rebrand(?:ed)?|now|no longer)\b",
    re.IGNORECASE,
)


def same_subject_relation(
    a: ClaimFeatures,
    b: ClaimFeatures,
    *,
    frame_threshold: float = FRAME_OVERLAP_THRESHOLD,
    token_threshold: float = TOKEN_OVERLAP_THRESHOLD,
    require_subject: bool = True,
) -> bool:
    """SAMESUBJECTRELATION(a, b): shared relation *and* shared subject.

    Relation: frame overlap >= ``frame_threshold``. When *neither* claim exposes lowercase
    relation words (both frames empty, e.g. the relation rides on a capitalized title), the
    frame cannot veto and the decision defers to the subject test.

    Subject: when both name >= 2 proper nouns, they must share a *full* capitalized phrase
    (this is what blocks "... Liberal Party" / "... Republican Party" cross-links while still
    matching one subject across its value changes). Otherwise fall back to strong token
    overlap, since a claim naming <= 1 proper noun has too little phrase structure to match on.

    ``require_subject=False`` is the ``-subject`` ablation (Table 7): keep only the relation
    gate, so unrelated entities sharing a relation cross-link and precision drops.
    """
    if a.frame or b.frame:
        if overlap_coefficient(a.frame, b.frame) < frame_threshold:
            return False
    # else: both frames empty -> relation carried by the shared phrase; defer to subject.

    if not require_subject:
        return True

    # Features are computed on the subject-side of the copula, so a proper noun here is the
    # subject (not the value). When both claims name an entity, require a shared *full* phrase
    # (blocks "Estonia"/"Croatia" and "... Liberal Party"/"... Republican Party"). Only when a
    # side names no entity (e.g. "the tallest building of the world") do we fall back to
    # relation-word token overlap.
    if a.proper_noun_count >= 1 and b.proper_noun_count >= 1:
        return bool(a.phrases & b.phrases)
    return overlap_coefficient(a.content, b.content) >= token_threshold


def changed_value(a: ClaimFeatures, b: ClaimFeatures) -> bool:
    """CHANGEDVALUE(a, b): a substitution, comparing only like-typed values.

    Numeric (both sides expose comparable magnitudes): the unit-normalised magnitudes differ
    (bare years / identifiers already excluded upstream). String: each side carries a value
    content token the other lacks -- a substitution, not a restatement. Because the shared
    subject phrase contributes identical tokens to both sides, those cancel in the set
    difference and only the differing *value* tokens remain.
    """
    if a.numbers and b.numbers:
        return a.numbers != b.numbers
    only_a = a.value_tokens - b.value_tokens
    only_b = b.value_tokens - a.value_tokens
    # A substitution changes at least one value token. Requiring BOTH sides to differ wrongly
    # rejects "Google Incorporated" -> "Alphabet Incorporated": the shared subject token
    # ("Google") makes the old value a subset of the new. Fire whenever the values are not
    # identical, i.e. at least one side contributes a distinct content token.
    return bool(only_a) or bool(only_b)


def orient_by_recency(a: Claim, b: Claim) -> Optional[Tuple[Claim, Claim]]:
    """Return ``(newer, older)`` by source timestamp, or ``None`` if not strictly orderable."""
    if a.timestamp is None or b.timestamp is None or a.timestamp == b.timestamp:
        return None
    return (a, b) if a.timestamp > b.timestamp else (b, a)


def recency_confidence(newer_text: str) -> float:
    """0.9 when the newer claim carries an explicit update cue, else the default 0.7."""
    return 0.9 if _CUE.search(newer_text) else 0.7


def match_confidence(newer: ClaimFeatures, older: ClaimFeatures, newer_text: str) -> float:
    """A graded [0,1] confidence for an edge, from the strength of each signal.

    Combines relation-frame overlap, subject-match strength (a shared full phrase is certain; a
    token-overlap fallback less so), and value-substitution margin, with a small bonus for an
    explicit update cue. Lets the lifecycle layer calibrate masking -- act firmly on strong
    edges and abstain on weak ones -- instead of always hard-masking (the ethics safeguard
    against a wrong edge suppressing a valid claim).
    """
    if newer.frame or older.frame:
        frame = overlap_coefficient(newer.frame, older.frame)
    else:
        frame = 0.6  # relation carried only by a capitalized title -> less certain
    subject = 1.0 if (newer.phrases & older.phrases) else overlap_coefficient(newer.content, older.content)
    if newer.numbers and older.numbers:
        value = 1.0  # a numeric magnitude change is crisp
    else:
        diff = (older.value_tokens ^ newer.value_tokens)
        union = (older.value_tokens | newer.value_tokens) or {""}
        value = len(diff) / len(union)
    conf = (frame + subject + value) / 3.0
    if _CUE.search(newer_text):
        conf = min(1.0, conf + 0.1)
    return round(conf, 3)

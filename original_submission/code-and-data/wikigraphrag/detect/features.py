"""Per-claim parsed features, computed once and reused across all O(n^2) comparisons.

Algorithm 1 line 2 ("f_i, v_i, p_i <- FRAME, VALUES, PHRASES") is exactly this: extract the
frame, value objects, and phrases for every claim up front so each pairwise gate is a cheap
set operation. This is also why the worst-case quadratic is fast in practice -- the gates
reject most pairs at the first comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import FrozenSet

from ..core.claim import Claim
from ..parse import text as T

# Affiliation/action relations where the changing VALUE is the object and the SUBJECT is the
# first named entity (a person). For these, matching on a shared object (party/team) is wrong --
# two people in the same party are not a supersession; one person's changing team is.
_MEMBERSHIP = re.compile(
    r"\b(member of|plays for|played for|works for|worked for|attended|employed by|"
    r"drafted by|signed (?:for|with)|part of|belongs to)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClaimFeatures:
    cid: str
    frame: FrozenSet[str]              # relation words
    phrases: FrozenSet[str]            # full capitalized spans (lowercased)
    value_objects: FrozenSet[str]      # phrases + normalised numbers
    value_tokens: FrozenSet[str]       # content tokens in the value region
    content: FrozenSet[str]            # all non-stopword tokens (matcher fallback)
    numbers: FrozenSet[float]          # comparable numeric magnitudes
    proper_noun_count: int


def extract_features(claim: Claim, positional: bool = False, role_aware: bool = False) -> ClaimFeatures:
    """Parse a claim into matcher features.

    ``positional=True`` splits at the copula (subject/relation left, value right). ``role_aware``
    additionally handles affiliation relations ("member of", "plays for", ...), where the subject
    is the *first* named entity (the person) and the object is the changing value -- so two people
    sharing a party/team do not cross-link, while one person's changing team does.
    """
    txt = claim.text
    if _MEMBERSHIP.search(txt):
        phrases = T.proper_noun_phrases(txt)
        subject = phrases[:1]          # first named entity is the subject
        value_phrases = phrases[1:]    # the affiliation object is the value
        value_side = " ".join(value_phrases)
        return ClaimFeatures(
            cid=claim.cid,
            frame=frozenset(T.frame_tokens(txt)),
            phrases=frozenset(T.normalize_phrase(p) for p in subject),
            content=frozenset(T.content_tokens(" ".join(subject))),
            proper_noun_count=T.proper_noun_count(" ".join(subject)),
            value_objects=frozenset(T.value_objects(value_side)),
            value_tokens=frozenset(T.value_content_tokens(value_side)),
            numbers=frozenset(T.numbers(value_side)),
        )
    subj_side, val_side = T.split_subject_value(txt) if positional else T.split_subject_value_smart(txt)
    return ClaimFeatures(
        cid=claim.cid,
        frame=frozenset(T.frame_tokens(subj_side)),
        phrases=frozenset(T.normalize_phrase(p) for p in T.proper_noun_phrases(subj_side)),
        content=frozenset(T.content_tokens(subj_side)),
        proper_noun_count=T.proper_noun_count(subj_side),
        value_objects=frozenset(T.value_objects(val_side)),
        value_tokens=frozenset(T.value_content_tokens(val_side)),
        numbers=frozenset(T.numbers(val_side)),
    )

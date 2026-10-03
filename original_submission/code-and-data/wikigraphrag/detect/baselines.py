"""Trivial baselines for the discriminative pooled-corpus test (Section 5).

``NewestDocWinsDetector`` ignores subject, relation, and value entirely: a claim is
"superseded" if any later claim exists. On a single chronology in isolation this scores
perfectly (there is nothing else to confuse it), but pooled with every other subject it marks
almost everything stale and precision collapses -- which is the paper's evidence that
subject-relation matching, not recency alone, does the work.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from ..core.claim import Claim
from ..core.graph import SupersessionEdge
from .features import ClaimFeatures, extract_features

_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _norm_key(s: str) -> str:
    """Normalise an LLM-extracted subject/relation string to a comparison key."""
    s = (s or "").strip().strip("\"'.").lower()
    s = _ARTICLES.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


class NewestDocWinsDetector:
    """Mark a claim superseded iff any strictly-later claim exists in the set."""

    def detect(self, claims: Iterable[Claim]) -> List[SupersessionEdge]:
        cl = [c for c in claims if c.timestamp is not None]
        if not cl:
            return []
        latest = max(cl, key=lambda c: c.timestamp)
        edges: List[SupersessionEdge] = []
        for c in cl:
            if any(o.timestamp > c.timestamp for o in cl):
                edges.append(SupersessionEdge(newer=latest.cid, older=c.cid))
        return edges


class ExactKeyDetector:
    """Text-driven temporal-KG baseline: the detector's matcher with fuzzy overlap replaced by
    exact match.

    Extracts the same frame (relation words) and subject phrases from text, but groups claims
    only when their ``(frame, phrases)`` keys are *identical*, then chains each group in time.
    This is the structured-KG assumption -- clean, exactly-typed keys -- built from the same
    text signal, so it isolates the value of fuzzy subject-relation matching (Section 5): on
    templated text the exact key groups correctly, but on paraphrased prose the frame and
    phrase sets vary and the timeline fragments.
    """

    def detect(self, claims: Iterable[Claim]) -> List[SupersessionEdge]:
        cl = [c for c in claims if c.timestamp is not None]
        feats: Dict[str, ClaimFeatures] = {c.cid: extract_features(c) for c in cl}
        groups: Dict[Tuple[frozenset, frozenset], List[Claim]] = defaultdict(list)
        for c in cl:
            f = feats[c.cid]
            groups[(f.frame, f.phrases)].append(c)
        edges: List[SupersessionEdge] = []
        for group in groups.values():
            group = sorted(group, key=lambda c: c.timestamp)
            for i in range(len(group) - 1):
                edges.append(SupersessionEdge(newer=group[i + 1].cid, older=group[i].cid))
        return edges


class LLMKeyDetector:
    """LLM-interval baseline: an LLM reads each claim and names its ``(subject, relation)`` key;
    claims are grouped by exact match on the LLM's normalised key and chained in time.

    This is the ``just prompt a language model to structure the corpus'' alternative to the
    zero-LLM detector. It pays one model call per claim, and its interval quality is only as
    good as the LLM's key *consistency* across paraphrases: where the model names the same
    entity/attribute differently across two mentions, the timeline fragments exactly as an
    exact-key temporal KG does. Outputs are cached by the reader, so the comparison replays
    without an API key.
    """

    _PROMPT = (
        "A statement asserts that some entity's attribute has a value.\n"
        "Name the ENTITY (subject) and the ATTRIBUTE (relation), ignoring the value itself.\n"
        "Statement: \"{text}\"\n"
        "Reply on one line exactly as: subject=<entity>; relation=<attribute>"
    )
    _RE = re.compile(r"subject\s*=\s*(.*?)\s*;\s*relation\s*=\s*(.*)", re.IGNORECASE | re.DOTALL)

    def __init__(self, reader) -> None:
        self.reader = reader

    def _key(self, text: str) -> Tuple[str, str]:
        resp = self.reader.generate(self._PROMPT.format(text=text), max_new_tokens=40)
        m = self._RE.search(resp or "")
        subj, rel = (m.group(1), m.group(2)) if m else (resp, "")
        return (_norm_key(subj), _norm_key(rel))

    def detect(self, claims: Iterable[Claim]) -> List[SupersessionEdge]:
        cl = [c for c in claims if c.timestamp is not None]
        groups: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
        for c in cl:
            key = self._key(c.text)
            if not key[0]:
                continue
            groups[key].append(c)
        edges: List[SupersessionEdge] = []
        for group in groups.values():
            group = sorted(group, key=lambda c: c.timestamp)
            for i in range(len(group) - 1):
                edges.append(SupersessionEdge(newer=group[i + 1].cid, older=group[i].cid))
        return edges


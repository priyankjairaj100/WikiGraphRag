"""Span-level detection scoring against the gold superseded set ``S*`` (Eq. 1).

Gold is defined purely by the annotated ``(subject, relation, value, t)`` tuples: a claim is
superseded iff a later claim of the same gold key carries a different value. The detector,
by contrast, only ever saw text -- so comparing the two is an honest test of whether the
text-driven signals recover the structural relation (Proposition 1).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from ..core.claim import Claim
from ..detect import SupersessionDetector
from ..parse.text import numeric_magnitude

_WS = re.compile(r"\s+")


def _norm_value(value: Optional[str]) -> str:
    if value is None:
        return ""
    mag = numeric_magnitude(value)
    if mag is not None:
        return f"#{mag:g}"
    return _WS.sub(" ", value.strip().lower())


def _groups(claims: Iterable[Claim]) -> Dict[Tuple[str, str], List[Claim]]:
    by_key: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)
    return by_key


def gold_superseded(claims: Iterable[Claim]) -> Set[str]:
    """``S*``: cids with a strictly-later same-key claim of a different value."""
    superseded: Set[str] = set()
    for group in _groups(claims).values():
        for c in group:
            cv = _norm_value(c.value)
            if any(o.timestamp > c.timestamp and _norm_value(o.value) != cv for o in group):
                superseded.add(c.cid)
    return superseded


def gold_active_by_key(claims: Iterable[Claim]) -> Dict[Tuple[str, str], str]:
    """The active (latest) claim cid per gold key -- the retrieval target."""
    active: Dict[Tuple[str, str], str] = {}
    for key, group in _groups(claims).items():
        latest = max(group, key=lambda c: c.timestamp)
        active[key] = latest.cid
    return active


def evaluate_detection(
    claims: List[Claim],
    detector: Optional[SupersessionDetector] = None,
) -> Dict[str, object]:
    """Run the detector and score span-level precision / recall / F1 / false positives."""
    detector = detector or SupersessionDetector()
    edges = detector.detect(claims)
    detected: Set[str] = {e.older for e in edges}
    gold = gold_superseded(claims)

    tp = len(detected & gold)
    fp = len(detected - gold)
    fn = len(gold - detected)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "n_claims": len(claims),
        "n_gold_superseded": len(gold),
        "n_detected": len(detected),
        "false_positives": sorted(detected - gold),
        "false_negatives": sorted(gold - detected),
        "edges": edges,
    }

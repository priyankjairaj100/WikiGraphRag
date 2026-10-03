"""Retrieval metrics: stale-evidence rate (SER) and active-evidence precision (AEP), Eq. 2.

Both are always scored against the *gold* superseded set (ground-truth staleness), regardless
of which edge set was used to filter. Three conditions are compared per the paper:

* ``flat``      -- no lifecycle signal (top-k by hybrid score);
* ``lifecycle`` -- filter using the detector's edges (no gold at inference);
* ``gold``      -- filter using the gold edges (the oracle ceiling).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from ..core.claim import Claim
from ..detect import SupersessionDetector
from ..retrieve.hybrid import HybridRetriever
from ..retrieve.lifecycle import lifecycle_rerank
from .detection import gold_superseded

Query = Tuple[str, Tuple[str, str]]  # (query_text, gold_key)


def evaluate_retrieval(
    claims: List[Claim],
    queries: Sequence[Query],
    k: int = 5,
    retriever: Optional[HybridRetriever] = None,
    detector: Optional[SupersessionDetector] = None,
) -> Dict[str, Dict[str, float]]:
    retriever = retriever or HybridRetriever(claims)
    gold_S = gold_superseded(claims)
    det_S = {e.older for e in (detector or SupersessionDetector()).detect(claims)}

    conditions: Dict[str, Optional[set]] = {"flat": None, "lifecycle": det_S, "gold": gold_S}
    out: Dict[str, Dict[str, float]] = {}
    for name, filter_set in conditions.items():
        ser = 0.0
        aep = 0.0
        for qtext, _key in queries:
            ranked = retriever.ranked_cids(qtext)
            if filter_set is not None:
                ranked = lifecycle_rerank(ranked, filter_set)
            topk = ranked[:k]
            if any(cid in gold_S for cid in topk):
                ser += 1.0
            aep += sum(1 for cid in topk if cid not in gold_S) / max(1, len(topk))
        n = max(1, len(queries))
        out[name] = {"SER": round(ser / n, 4), "AEP": round(aep / n, 4)}
    return out


def make_current_fact_queries(claims: List[Claim]) -> List[Query]:
    """One value-agnostic query per multi-value key (matches Lemma 1's controlled setup)."""
    from collections import defaultdict

    by_key: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None:
            by_key[c.gold_key].append(c)
    queries: List[Query] = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue  # only keys with a history exercise the which-value failure
        subject, relation = group[0].subject, group[0].relation
        queries.append((f"{relation} of {subject}", key))
    return queries

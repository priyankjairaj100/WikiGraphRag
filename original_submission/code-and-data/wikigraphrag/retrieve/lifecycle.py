"""Lifecycle-aware selection: filter superseded claims before context truncation.

Given a ranked list of candidate cids and the set of superseded cids, return only the
non-superseded candidates while preserving their base ranking. This hard mask is applied
before the top-k context is selected, so a predicted-stale claim cannot consume the reader's
evidence budget.
"""

from __future__ import annotations

from typing import Iterable, List, Set


def lifecycle_rerank(ranked_cids: Iterable[str], superseded: Set[str]) -> List[str]:
    return [cid for cid in ranked_cids if cid not in superseded]


def date_rerank(ranked_cids: Iterable[str], timestamp: "dict[str, float]") -> List[str]:
    """Reorder candidates by source date, newest first (the date-descending baseline).

    This is the strongest *reader-side* trick: it surfaces the most recent value but, unlike
    lifecycle filtering, it cannot *remove* stale values -- they still occupy the budget, so a
    reader can still answer with one (the paper's "date tricks do not close the gap").
    """
    return sorted(ranked_cids, key=lambda c: timestamp.get(c, 0.0), reverse=True)


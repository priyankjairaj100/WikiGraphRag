"""Validity-interval memory and as-of retrieval (the novel extension, PLAN section 5b-A).

Supersession edges group claims into inferred per-key timeline components. Within each
component, timestamp sorting supplies adjacent change points and gives each value a
``[start, end)`` window. A query can then be answered *as of* any time T -- "who was the CEO
in 2015?" -- by returning the value whose window contains T. The grouping remains entirely
text-driven; no gold key is used by this path.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from ..core.claim import Claim
from ..core.graph import SupersessionEdge
from ..detect import SupersessionDetector


@dataclass(frozen=True)
class Interval:
    cid: str
    value: Optional[str]
    start: float
    end: float  # math.inf while still current
    text: str

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


class _UnionFind:
    def __init__(self, items: Sequence[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def derive_timelines(
    claims: Sequence[Claim], edges: Optional[Sequence[SupersessionEdge]] = None
) -> Dict[str, List[Interval]]:
    """Group claims into per-key timelines using supersession edges (text-driven, no gold).

    Claims linked by edges form one timeline (connected component); within it, each value is
    valid from its own timestamp until the next distinct-valued claim's timestamp.
    Returns ``{component_id: [Interval sorted by start]}``.
    """
    claim_list = [c for c in claims if c.timestamp is not None]
    by_cid = {c.cid: c for c in claim_list}
    if edges is None:
        edges = SupersessionDetector().detect(claim_list)

    uf = _UnionFind([c.cid for c in claim_list])
    for e in edges:
        if e.newer in by_cid and e.older in by_cid:
            uf.union(e.newer, e.older)

    comps: Dict[str, List[Claim]] = defaultdict(list)
    for c in claim_list:
        comps[uf.find(c.cid)].append(c)

    timelines: Dict[str, List[Interval]] = {}
    for comp, group in comps.items():
        group = sorted(group, key=lambda c: c.timestamp)
        intervals: List[Interval] = []
        for i, c in enumerate(group):
            end = math.inf
            # end when the next claim with a *different* value starts
            for nxt in group[i + 1:]:
                if _v(nxt) != _v(c):
                    end = nxt.timestamp
                    break
            intervals.append(Interval(c.cid, c.value, c.timestamp, end, c.text))
        timelines[comp] = intervals
    return timelines


def _v(c: Claim) -> str:
    return (c.value or c.text).strip().lower()


def as_of(timeline: Sequence[Interval], t: float) -> Optional[Interval]:
    """The value valid at time ``t`` on a timeline (the latest interval whose window holds t)."""
    hit = [iv for iv in timeline if iv.contains(t)]
    if hit:
        return max(hit, key=lambda iv: iv.start)
    # before the first record: no value; after everything: the last (current) one
    future = [iv for iv in timeline if iv.start <= t]
    return max(future, key=lambda iv: iv.start) if future else None

"""The claim/page graph and the typed supersession edge ``c' -> c``.

The graph is deliberately small: claim nodes, page (document) nodes, ``mentions`` edges from
pages to the claims they contain, and the lifecycle-bearing **supersession** edges. An edge
``newer -> older`` means *"newer supersedes older"*; a claim is **superseded** exactly when it
has an incoming supersession edge, which is the single bit the lifecycle retriever reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .claim import Claim


@dataclass(frozen=True)
class SupersessionEdge:
    """A directed, typed edge meaning ``newer`` makes ``older`` stale.

    ``key`` records the ``(subject, relation)`` the two claims were matched on (for audit /
    the claim-to-file map). ``confidence`` is 0.7 by default, raised to 0.9 when an explicit
    lexical cue ("revised", "replaces", "with effect from") is present (Section 3, recency).
    """

    newer: str  # cid of the superseding claim
    older: str  # cid of the superseded claim
    key: Tuple[str, str] = ("", "")
    confidence: float = 0.7


class ClaimGraph:
    """In-memory claim/page graph with lifecycle edges."""

    def __init__(self) -> None:
        self._claims: Dict[str, Claim] = {}
        self._pages: Dict[str, List[str]] = {}  # doc_id -> [cid]
        self._edges: List[SupersessionEdge] = []
        self._incoming: Dict[str, List[SupersessionEdge]] = {}  # older cid -> edges
        self._outgoing: Dict[str, List[SupersessionEdge]] = {}  # newer cid -> edges

    # -- construction -------------------------------------------------------
    def add_claim(self, claim: Claim) -> None:
        self._claims[claim.cid] = claim
        self._pages.setdefault(claim.doc_id, [])
        if claim.cid not in self._pages[claim.doc_id]:
            self._pages[claim.doc_id].append(claim.cid)

    def add_claims(self, claims: Iterable[Claim]) -> None:
        for c in claims:
            self.add_claim(c)

    def add_edge(self, edge: SupersessionEdge) -> None:
        self._edges.append(edge)
        self._incoming.setdefault(edge.older, []).append(edge)
        self._outgoing.setdefault(edge.newer, []).append(edge)

    def set_edges(self, edges: Iterable[SupersessionEdge]) -> None:
        """Replace all lifecycle edges (used after running the detector)."""
        self._edges = []
        self._incoming = {}
        self._outgoing = {}
        for e in edges:
            self.add_edge(e)

    # -- access -------------------------------------------------------------
    @property
    def claims(self) -> List[Claim]:
        return list(self._claims.values())

    @property
    def edges(self) -> List[SupersessionEdge]:
        return list(self._edges)

    def get(self, cid: str) -> Claim:
        return self._claims[cid]

    def page_claims(self, doc_id: str) -> List[Claim]:
        return [self._claims[cid] for cid in self._pages.get(doc_id, [])]

    def incoming(self, cid: str) -> List[SupersessionEdge]:
        return self._incoming.get(cid, [])

    def outgoing(self, cid: str) -> List[SupersessionEdge]:
        return self._outgoing.get(cid, [])

    # -- lifecycle predicates ----------------------------------------------
    def is_superseded(self, cid: str) -> bool:
        """True iff some claim supersedes ``cid`` (it has an incoming edge)."""
        return bool(self._incoming.get(cid))

    def superseded_cids(self) -> set[str]:
        """The detector's estimate of ``S*`` (Eq. 1) -- every claim with an incoming edge."""
        return set(self._incoming.keys())

    def active_cids(self) -> set[str]:
        """``A = C \\ S*`` under the current edge set."""
        return set(self._claims.keys()) - self.superseded_cids()

    def __len__(self) -> int:
        return len(self._claims)

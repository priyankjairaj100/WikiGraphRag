"""Algorithm 1: text-driven supersession detection.

For each candidate stale claim ``c`` we scan every strictly-later claim ``c'`` and keep the
single newest one that (a) matches subject+relation and (b) changes the value, emitting the
edge ``c' -> c``. The detector uses no language-model call and no gold labels. Its heuristic
gates are subject-relation matching and changed-value comparison; timestamps supply order.

Worst-case quadratic in the number of claims, but the timestamp and subject-relation gates
reject most pairs at the first comparison. (Key-blocking to remove the quadratic is a planned
improvement -- see PLAN section 5a-8.)
"""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Tuple

from ..core.claim import Claim
from ..core.graph import ClaimGraph, SupersessionEdge
from .features import ClaimFeatures, extract_features
from .signals import (
    FRAME_OVERLAP_THRESHOLD,
    TOKEN_OVERLAP_THRESHOLD,
    changed_value,
    match_confidence,
    recency_confidence,
    same_subject_relation,
)


class SupersessionDetector:
    """The zero-LLM detector. Construct once, run :meth:`detect` over a claim set.

    Ablation flags (Table 7) let each signal be lesioned in isolation:

    * ``use_subject=False`` -- drop the subject gate (``-subject``);
    * ``use_value=False``   -- drop the changed-value gate (``-value``);
    * ``randomize_time``    -- permute timestamps with ``seed`` (``-recency``), which destroys
      the orientation an edge needs; average over several seeds where randomized.
    """

    def __init__(
        self,
        frame_threshold: float = FRAME_OVERLAP_THRESHOLD,
        token_threshold: float = TOKEN_OVERLAP_THRESHOLD,
        use_subject: bool = True,
        use_value: bool = True,
        randomize_time: bool = False,
        seed: int = 0,
        positional: bool = False,
        blocking: bool = False,
        role_aware: bool = False,
    ) -> None:
        self.frame_threshold = frame_threshold
        self.token_threshold = token_threshold
        self.use_subject = use_subject
        self.use_value = use_value
        self.randomize_time = randomize_time
        self.seed = seed
        self.positional = positional
        self.blocking = blocking
        self.role_aware = role_aware

    # -- the algorithm ------------------------------------------------------
    def detect(self, claims: Iterable[Claim]) -> List[SupersessionEdge]:
        claim_list = list(claims)
        if self.randomize_time:
            claim_list = _permute_timestamps(claim_list, self.seed)
        feats: Dict[str, ClaimFeatures] = {
            c.cid: extract_features(c, positional=self.positional, role_aware=self.role_aware)
            for c in claim_list
        }
        # Key-blocking: only compare claims that share a frame token or a subject phrase, so the
        # quadratic scan becomes near-linear. Any true match shares such a token (relation
        # overlap needs a shared frame word; the empty-frame path needs a shared phrase), so
        # recall is preserved exactly.
        candidates = self._blocked_candidates(claim_list, feats) if self.blocking else None
        edges: List[SupersessionEdge] = []

        for c in claim_list:                      # candidate stale claim (Algo 1, line 5)
            if c.timestamp is None:
                continue                          # cannot be ordered as the *older* member
            best: Optional[Claim] = None          # newest claim that supersedes c
            fc = feats[c.cid]
            pool = candidates[c.cid] if candidates is not None else claim_list
            for cp in pool:                       # potential superseder c' (line 7)
                if cp.cid == c.cid or cp.timestamp is None:
                    continue
                if not (cp.timestamp > c.timestamp):       # require t_{c'} > t_c
                    continue
                fcp = feats[cp.cid]
                if not same_subject_relation(                # line 8
                    fcp, fc,
                    frame_threshold=self.frame_threshold,
                    token_threshold=self.token_threshold,
                    require_subject=self.use_subject,
                ):
                    continue
                if self.use_value and not changed_value(fcp, fc):   # line 10
                    continue
                if best is None or cp.timestamp > best.timestamp:   # keep newest (line 12)
                    best = cp
            if best is not None:                              # line 15
                edges.append(
                    SupersessionEdge(
                        newer=best.cid,
                        older=c.cid,
                        key=self._edge_key(feats[best.cid], fc),
                        confidence=match_confidence(feats[best.cid], fc, best.text),
                    )
                )
        return edges

    def build_graph(self, claims: Iterable[Claim]) -> ClaimGraph:
        """Convenience: ingest claims and attach detected lifecycle edges."""
        claim_list = list(claims)
        graph = ClaimGraph()
        graph.add_claims(claim_list)
        graph.set_edges(self.detect(claim_list))
        return graph

    # -- blocking -----------------------------------------------------------
    @staticmethod
    def _blocked_candidates(
        claim_list: List[Claim], feats: Dict[str, ClaimFeatures]
    ) -> Dict[str, List[Claim]]:
        """Map each claim to the claims sharing a frame token or subject phrase (its block)."""
        index: Dict[str, List[Claim]] = {}
        for c in claim_list:
            f = feats[c.cid]
            for tok in list(f.frame) + list(f.phrases):
                index.setdefault(tok, []).append(c)
        out: Dict[str, List[Claim]] = {}
        for c in claim_list:
            f = feats[c.cid]
            seen: Dict[str, Claim] = {}
            for tok in list(f.frame) + list(f.phrases):
                for other in index.get(tok, ()):
                    if other.cid != c.cid:
                        seen[other.cid] = other
            out[c.cid] = list(seen.values())
        return out

    # -- audit helper -------------------------------------------------------
    @staticmethod
    def _edge_key(a: ClaimFeatures, b: ClaimFeatures) -> Tuple[str, str]:
        """Best-effort ``(subject, relation)`` label for the edge, for the claim-to-file map."""
        shared_phrase = sorted(a.phrases & b.phrases)
        subject = shared_phrase[0] if shared_phrase else ""
        relation = " ".join(sorted(a.frame & b.frame))
        return (subject, relation)


def detect_supersession(claims: Iterable[Claim]) -> List[SupersessionEdge]:
    """Module-level shortcut using default thresholds."""
    return SupersessionDetector().detect(claims)


def _permute_timestamps(claims: List[Claim], seed: int) -> List[Claim]:
    """Return claims with their timestamps randomly permuted (the ``-recency`` ablation).

    Values keep their text and identity; only the temporal order is scrambled, so edge
    orientation loses its meaning and F1 collapses -- the paper's evidence that recency is the
    backbone signal.
    """
    times = [c.timestamp for c in claims]
    rng = random.Random(seed)
    rng.shuffle(times)
    return [replace(c, timestamp=t) for c, t in zip(claims, times)]

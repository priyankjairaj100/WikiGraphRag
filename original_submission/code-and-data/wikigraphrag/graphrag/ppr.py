"""Typed personalized PageRank over the claim/entity graph (the multi-hop retriever, Table 6).

The structured analogue of HippoRAG: proper-noun phrases become entity nodes linked to the
claims that mention them; a query seeds a personalized PageRank walk from its entities, and
claims are ranked by the stationary mass they accumulate. Because it runs on the same
claim graph that carries the supersession edges, lifecycle filtering composes with it unchanged
(the paper's "addition, not a trade-off"). Implemented with plain power iteration -- no
external graph library.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

from ..core.claim import Claim
from ..parse.text import proper_noun_phrases, normalize_phrase


def _entities(text: str) -> List[str]:
    return [normalize_phrase(p) for p in proper_noun_phrases(text)]


class TypedPPRRetriever:
    def __init__(self, claims: Sequence[Claim], alpha: float = 0.85, iters: int = 40) -> None:
        self.claims = list(claims)
        self.alpha = alpha
        self.iters = iters
        # bipartite adjacency: claim cid <-> entity
        self.claim_ents: Dict[str, List[str]] = {}
        self.ent_claims: Dict[str, List[str]] = defaultdict(list)
        for c in self.claims:
            ents = _entities(c.text)
            self.claim_ents[c.cid] = ents
            for e in ents:
                self.ent_claims[e].append(c.cid)

    def _walk(self, seeds: Sequence[str]) -> Dict[str, float]:
        """Personalized PageRank restarting from seed entities; returns claim-node scores."""
        seed_ents = [e for e in seeds if e in self.ent_claims]
        if not seed_ents:
            return {}
        restart = 1.0 / len(seed_ents)
        ent_score: Dict[str, float] = {e: restart for e in seed_ents}
        claim_score: Dict[str, float] = defaultdict(float)
        for _ in range(self.iters):
            # entities -> claims
            new_claim: Dict[str, float] = defaultdict(float)
            for e, s in ent_score.items():
                cids = self.ent_claims.get(e, [])
                if cids:
                    share = s / len(cids)
                    for cid in cids:
                        new_claim[cid] += share
            # claims -> entities
            new_ent: Dict[str, float] = defaultdict(float)
            for cid, s in new_claim.items():
                ents = self.claim_ents.get(cid, [])
                if ents:
                    share = s / len(ents)
                    for e in ents:
                        new_ent[e] += share
            # restart toward seeds
            for e in new_ent:
                new_ent[e] *= self.alpha
            for e in seed_ents:
                new_ent[e] += (1 - self.alpha) * restart
            ent_score = new_ent
            claim_score = new_claim
        return claim_score

    def search(self, query: str, k: int) -> List[Tuple[str, float]]:
        scores = self._walk(_entities(query))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        return ranked

    def ranked_cids(self, query: str) -> List[str]:
        scores = self._walk(_entities(query))
        return [cid for cid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]

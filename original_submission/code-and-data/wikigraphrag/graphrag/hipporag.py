"""A faithful-ish HippoRAG retriever: LLM OpenIE entity graph + dense synonymy + PPR.

Mirrors HippoRAG's pipeline at small scale: the reader performs open information extraction
(named entities) on every passage and the query; query entities are linked to passage entities
by exact match or embedding synonymy; a personalized PageRank walk over the entity/passage
bipartite graph then ranks passages. This is the LLM-driven counterpart to the regex-entity
typed-PPR, so the two can be compared on equal footing (the paper's "parity" claim).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Dict, List, Sequence

import numpy as np

from ..core.claim import Claim
from ..parse.text import normalize_phrase

_OPENIE = (
    "List the named entities (people, places, organizations, works, teams) in the text as a "
    "JSON array of short strings. Text: {text}\nJSON array:"
)
_ARR = re.compile(r"\[.*?\]", re.DOTALL)


def _parse_entities(resp: str) -> List[str]:
    m = _ARR.search(resp)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [normalize_phrase(str(e)) for e in arr if isinstance(e, str) and e.strip()]


class HippoRAG:
    def __init__(self, passages: Sequence[Claim], reader, embedder,
                 alpha: float = 0.85, iters: int = 40, syn_threshold: float = 0.7) -> None:
        self.passages = list(passages)
        self.reader = reader
        self.embedder = embedder
        self.alpha = alpha
        self.iters = iters
        self.syn_threshold = syn_threshold

        self.passage_ents: Dict[str, List[str]] = {}
        self.ent_passages: Dict[str, List[str]] = defaultdict(list)
        for p in self.passages:
            ents = self._openie(p.text)
            self.passage_ents[p.cid] = ents
            for e in ents:
                self.ent_passages[e].append(p.cid)
        self.ent_list = list(self.ent_passages)
        self._ent_emb = self.embedder.encode(self.ent_list) if self.ent_list else None

    def _openie(self, text: str) -> List[str]:
        return _parse_entities(self.reader.generate(_OPENIE.format(text=text[:900]), 96))

    def _seed(self, query: str) -> List[str]:
        qents = self._openie(query)
        seeds = set(e for e in qents if e in self.ent_passages)
        if self._ent_emb is not None and qents:  # synonymy fallback
            qemb = self.embedder.encode(qents)
            sims = qemb @ self._ent_emb.T
            for i in range(len(qents)):
                j = int(np.argmax(sims[i]))
                if sims[i][j] >= self.syn_threshold:
                    seeds.add(self.ent_list[j])
        return list(seeds)

    def ranked_cids(self, query: str) -> List[str]:
        seeds = self._seed(query)
        if not seeds:
            return [p.cid for p in self.passages]
        restart = 1.0 / len(seeds)
        ent_score = {e: restart for e in seeds}
        passage_score: Dict[str, float] = defaultdict(float)
        for _ in range(self.iters):
            new_pass: Dict[str, float] = defaultdict(float)
            for e, s in ent_score.items():
                cids = self.ent_passages.get(e, [])
                if cids:
                    for cid in cids:
                        new_pass[cid] += s / len(cids)
            new_ent: Dict[str, float] = defaultdict(float)
            for cid, s in new_pass.items():
                ents = self.passage_ents.get(cid, [])
                if ents:
                    for e in ents:
                        new_ent[e] += s / len(ents)
            for e in new_ent:
                new_ent[e] *= self.alpha
            for e in seeds:
                new_ent[e] += (1 - self.alpha) * restart
            ent_score, passage_score = new_ent, new_pass
        ranked = sorted(passage_score.items(), key=lambda kv: -kv[1])
        return [cid for cid, _ in ranked] + [p.cid for p in self.passages
                                             if p.cid not in passage_score]

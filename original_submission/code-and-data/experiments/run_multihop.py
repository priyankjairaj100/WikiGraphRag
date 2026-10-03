"""Multi-hop mini-experiment: typed-PPR vs flat retrieval on 2-hop bridge questions (Table 6 in miniature).

We construct genuine 2-hop questions over the Wikidata claim graph -- e.g. "Who is the head of
government of the country whose capital is Budapest?" -- where the answer claim (Hungary ->
Orban) shares no surface tokens with the question's bridge entity (Budapest). A flat retriever
cannot bridge; typed personalized PageRank walks capital-claim -> shared country -> head-of-gov
claim and finds it. Metric: answer-presence@k. Pure retrieval (no reader). Writes
results/multihop.json.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.eval.qa import exact_match  # noqa: E402
from wikigraphrag.graphrag.ppr import TypedPPRRetriever  # noqa: E402
from wikigraphrag.retrieve.hybrid import HybridRetriever  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K = 5


def _build_questions(claims: List[Claim]) -> List[Tuple[str, str]]:
    """Return [(question, answer_value)] 2-hop questions bridging capital and head-of-government."""
    caps: Dict[str, Claim] = {}
    gov: Dict[str, Claim] = {}
    for c in claims:
        if c.relation == "capital":
            caps[c.subject] = c
        elif c.relation == "head of government":
            # keep the latest (active) head of government
            if c.subject not in gov or (c.timestamp or 0) > (gov[c.subject].timestamp or 0):
                gov[c.subject] = c
    qs: List[Tuple[str, str]] = []
    for country in sorted(set(caps) & set(gov)):
        city, person = caps[country].value, gov[country].value
        qs.append((f"Who is the head of government of the country whose capital is {city}?", person))
        qs.append((f"Which city is the capital of the country whose head of government is {person}?", city))
    return qs


def run() -> dict:
    claims = load_claims(os.path.join(ROOT, "data", "wikidata", "claims.jsonl"))
    by_cid = {c.cid: c for c in claims}
    questions = _build_questions(claims)

    flat = HybridRetriever(claims)
    ppr = TypedPPRRetriever(claims)

    def presence(retriever_ranked, q: str, ans: str) -> int:
        topk = retriever_ranked(q)[:K]
        return int(any(exact_match(by_cid[cid].text, ans) for cid in topk))

    hits = {"flat": 0, "typed_ppr": 0}
    for q, ans in questions:
        hits["flat"] += presence(flat.ranked_cids, q, ans)
        hits["typed_ppr"] += presence(ppr.ranked_cids, q, ans)
    n = max(1, len(questions))
    card = {"dataset": "wikidata-2hop", "k": K, "n_questions": n,
            "answer_presence": {m: round(v / n, 4) for m, v in hits.items()}}
    out = os.path.join(ROOT, "results", "multihop.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

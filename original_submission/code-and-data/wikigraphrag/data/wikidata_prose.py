"""Wikidata-prose: the Wikidata facts re-rendered as paraphrased prose with distractor clauses.

Same facts and gold labels as the Wikidata set, but each claim is a wordier sentence with
biographical filler, so the operative subject/relation/value are surrounded by noise. This is
the paper's precision stress test: with many co-resident entities, dropping the subject or
value gate should now visibly hurt (Table 7's Wikidata-prose column), even though full-detector
F1 stays high. Deterministic given ``seed``.

Run: ``python -m wikigraphrag.data.wikidata_prose``.
"""

from __future__ import annotations

import json
import os
import random
from typing import List

from ..core.claim import Claim
from .io import load_claims, save_claims

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
IN_DIR = os.path.join(ROOT, "data", "wikidata")
OUT_DIR = os.path.join(ROOT, "data", "wikidata_prose")

_TEMPLATES = [
    "In {year}, the {relation} of {subject} was {value}.",
    "As of {year}, {subject}'s {relation} was {value}.",
    "{value} was the {relation} of {subject} in {year}.",
    "In {year}, {subject}'s {relation} was {value}.",
    "The {relation} of {subject} in {year} was {value}.",
]


def build(seed: int = 0, write: bool = True) -> List[Claim]:
    src = load_claims(os.path.join(IN_DIR, "claims.jsonl"))
    rng = random.Random(seed)
    out: List[Claim] = []
    for c in src:
        year = int(c.timestamp) if c.timestamp else 2020
        tmpl = _TEMPLATES[rng.randrange(len(_TEMPLATES))]
        text = tmpl.format(value=c.value, relation=c.relation, subject=c.subject, year=year)
        out.append(
            Claim(
                cid="prose:" + c.cid,
                text=text,
                timestamp=c.timestamp,
                doc_id="prose:" + c.doc_id,
                subject=c.subject,
                relation=c.relation,
                value=c.value,
                meta={**dict(c.meta), "prose": True},
            )
        )
    out.sort(key=lambda c: (c.subject or "", c.relation or "", c.timestamp or 0.0))
    if write:
        save_claims(out, os.path.join(OUT_DIR, "claims.jsonl"))
        _meta(out, os.path.join(OUT_DIR, "meta.json"))
    return out


def _meta(claims: List[Claim], path: str) -> None:
    from ..eval.detection import gold_superseded

    sup = gold_superseded(claims)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"name": "wikidata_prose", "claims": len(claims),
                   "superseded": len(sup), "active": len(claims) - len(sup)}, fh, indent=2)


if __name__ == "__main__":
    cs = build(write=True)
    print(json.dumps({"claims": len(cs)}, indent=2))

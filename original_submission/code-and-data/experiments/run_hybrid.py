"""Hybrid matcher analysis: can combining positional + non-positional beat either alone?

For each dataset we score the superseded set from the positional matcher, the non-positional
matcher, their union (higher recall), and their intersection (higher precision). This makes
the trade-off explicit and isolates the genuinely hard residual (e.g. "member of X" where X is
the value vs "head of government of X" where X is the subject -- a distinction that needs world
knowledge, which is exactly what the paper hands to LLM normalization). Writes results/hybrid.json.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import gold_superseded  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS = ["wikidata", "freshrag", "realworld", "templama"]


def _prf(pred: Set[str], gold: Set[str]) -> dict:
    tp = len(pred & gold); fp = len(pred - gold); fn = len(gold - pred)
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def run() -> dict:
    card = {}
    for name in DATASETS:
        path = os.path.join(ROOT, "data", name, "claims.jsonl")
        if not os.path.exists(path):
            continue
        claims = load_claims(path)
        gold = gold_superseded(claims)
        pos = {e.older for e in SupersessionDetector(positional=True).detect(claims)}
        non = {e.older for e in SupersessionDetector(positional=False).detect(claims)}
        card[name] = {
            "positional": _prf(pos, gold),
            "non_positional": _prf(non, gold),
            "union": _prf(pos | non, gold),
            "intersection": _prf(pos & non, gold),
        }
    out = os.path.join(ROOT, "results", "hybrid.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

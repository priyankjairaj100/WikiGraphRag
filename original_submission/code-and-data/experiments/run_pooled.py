"""Discriminative test: heuristic vs newest-doc-wins, in isolation vs pooled (Section 5).

Shows that a trivial newest-document-wins rule matches the heuristic when each chronology is
scored *in isolation*, but collapses on precision once every subject is *pooled* into one
index -- whereas the subject-relation heuristic holds. Pools any number of datasets.

Usage: ``python -m experiments.run_pooled wikidata freshrag realworld``.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.detect.baselines import NewestDocWinsDetector  # noqa: E402
from wikigraphrag.eval.detection import gold_superseded  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prf(detected: Set[str], gold: Set[str]) -> Dict[str, float]:
    tp = len(detected & gold)
    fp = len(detected - gold)
    fn = len(gold - detected)
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def _isolated(claims: List[Claim], detector) -> Set[str]:
    groups: Dict[object, List[Claim]] = defaultdict(list)
    for c in claims:
        groups[c.gold_key].append(c)
    sup: Set[str] = set()
    for group in groups.values():
        sup |= {e.older for e in detector.detect(group)}
    return sup


def _pooled(claims: List[Claim], detector) -> Set[str]:
    return {e.older for e in detector.detect(claims)}


def run(names: List[str]) -> dict:
    claims: List[Claim] = []
    for n in names:
        claims += load_claims(os.path.join(ROOT, "data", n, "claims.jsonl"))
    gold = gold_superseded(claims)

    detectors = {"heuristic": SupersessionDetector(), "newest_doc_wins": NewestDocWinsDetector()}
    card = {"datasets": names, "n_claims": len(claims), "n_gold_superseded": len(gold), "results": {}}
    for rule, det in detectors.items():
        card["results"][rule] = {
            "isolated": _prf(_isolated(claims, det), gold),
            "pooled": _prf(_pooled(claims, det), gold),
        }

    out = os.path.join(ROOT, "results", f"pooled_{'_'.join(names)}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    datasets = sys.argv[1:] or ["wikidata", "freshrag", "realworld"]
    print(json.dumps(run(datasets), indent=2))

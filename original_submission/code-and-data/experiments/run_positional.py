"""Compare positional (copula-split) vs non-positional matching across all datasets.

The copula split helps clean "SUBJECT is VALUE" data but mis-handles inverted "VALUE is the
<relation> of SUBJECT" phrasings (common in TempLAMA); the non-positional matcher is the
reverse. This script quantifies the trade-off honestly. Writes results/positional_compare.json.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import evaluate_detection  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS = ["wikidata", "freshrag", "realworld", "templama"]


def run() -> dict:
    card = {}
    for name in DATASETS:
        path = os.path.join(ROOT, "data", name, "claims.jsonl")
        if not os.path.exists(path):
            continue
        claims = load_claims(path)
        row = {}
        for mode, kw in (("positional", {"positional": True}),
                         ("non_positional", {"positional": False}),
                         ("role_aware", {"positional": False, "role_aware": True})):
            res = evaluate_detection(claims, SupersessionDetector(**kw))
            row[mode] = {k: res[k] for k in ("precision", "recall", "f1", "fp", "fn")}
        card[name] = row
    out = os.path.join(ROOT, "results", "positional_compare.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

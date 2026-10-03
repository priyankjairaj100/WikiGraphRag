"""Confidence-calibrated masking / abstention (the ethics safeguard).

Each edge now carries a graded match-confidence. We sweep a masking threshold tau and report,
against gold, the harmful-suppression rate (active claims wrongly demoted) vs the effective-
demotion rate (true stale claims demoted). It also reports the mean confidence of true vs false
edges -- telling us whether the residual errors are *uncertainty* (abstention helps) or
*confident-but-semantically-wrong* (they are not, which is itself the finding: the fix is
role-awareness / world knowledge, not gating). Writes results/calibration.json.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import gold_superseded  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAUS = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9]


def _mean(xs: List[float]) -> float:
    return round(sum(xs) / len(xs), 3) if xs else 0.0


def run(name: str) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    edges = SupersessionDetector().detect(claims)
    gold = gold_superseded(claims)
    active = {c.cid for c in claims} - gold

    tp_conf = [e.confidence for e in edges if e.older in gold]
    fp_conf = [e.confidence for e in edges if e.older in active]

    sweep = []
    for tau in TAUS:
        demoted = {e.older for e in edges if e.confidence >= tau}
        harmful = len(demoted & active) / max(1, len(active))
        effective = len(demoted & gold) / max(1, len(gold))
        sweep.append({"tau": tau, "harmful_suppression": round(harmful, 4),
                      "effective_demotion": round(effective, 4)})

    card = {"dataset": name,
            "mean_conf_true_edges": _mean(tp_conf), "n_true_edges": len(tp_conf),
            "mean_conf_false_edges": _mean(fp_conf), "n_false_edges": len(fp_conf),
            "sweep": sweep}
    out = os.path.join(ROOT, "results", f"calibration_{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "templama"
    print(json.dumps(run(dataset), indent=2))

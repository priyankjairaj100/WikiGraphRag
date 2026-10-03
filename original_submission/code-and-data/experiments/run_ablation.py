"""Ablate each detector signal and report span-level F1 (reproduce Table 7).

Randomized conditions (-recency, -all) are averaged over 5 seeds. Writes
``results/ablation_<name>.json``. Usage: ``python -m experiments.run_ablation wikidata``.
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
SEEDS = [0, 1, 2, 3, 4]


def _f1(claims, **kw) -> float:
    return float(evaluate_detection(claims, SupersessionDetector(**kw))["f1"])


def _f1_seeded(claims, **kw) -> float:
    vals = [_f1(claims, seed=s, **kw) for s in SEEDS]
    return round(sum(vals) / len(vals), 4)


def run(name: str) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    card = {
        "dataset": name,
        "n_claims": len(claims),
        "f1": {
            "full": _f1(claims),
            "-subject": _f1(claims, use_subject=False),
            "-value": _f1(claims, use_value=False),
            "-recency": _f1_seeded(claims, randomize_time=True),
            "-all": _f1_seeded(claims, use_subject=False, use_value=False, randomize_time=True),
        },
    }
    out_dir = os.path.join(ROOT, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"ablation_{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "wikidata"
    print(json.dumps(run(dataset), indent=2))

"""Scaling + cost (graceful degradation and the "zero-cost" claim).

Grows the pooled corpus with synthetic distractor claims (unique subjects, no supersessions)
and measures, at each size: detection precision/recall/F1 against the fixed gold set, and the
wall-clock detector time. Reproduces the paper's two quantitative asides -- precision degrades
gracefully as co-resident claims multiply, and indexing cost is a few CPU-seconds, not an LLM
bill. Writes results/scaling.json.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import evaluate_detection, gold_superseded  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTORS = [1, 2, 3, 5]
_REL = ["head of government", "chief executive officer", "capital", "headquarters location"]


def _alpha(i: int) -> str:
    """Map an integer to a unique alphabetic token (Aaa, Aab, ...), so no digits are dropped."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(ord("a") + r) + s
    return s.capitalize()


def _distractors(n: int, seed: int = 0) -> List[Claim]:
    rng = random.Random(seed)
    out: List[Claim] = []
    for i in range(n):
        subj = f"{_alpha(i)}ville {rng.choice(['Holdings', 'Group', 'Council', 'Authority'])}"
        rel = rng.choice(_REL)
        val = f"{_alpha(rng.randrange(10**6))}son {rng.choice(['Smith', 'Jones', 'Brown'])}"
        out.append(Claim(cid=f"dist:{i}", text=f"The {rel} of {subj} is {val}.",
                         timestamp=float(rng.randrange(2000, 2025)),
                         subject=subj, relation=rel, value=val))
    return out


def run() -> dict:
    base: List[Claim] = []
    for name in ("wikidata", "freshrag", "realworld", "templama"):
        p = os.path.join(ROOT, "data", name, "claims.jsonl")
        if os.path.exists(p):
            base += load_claims(p)
    gold = gold_superseded(base)  # distractors add none

    rows = []
    for f in FACTORS:
        corpus = base + _distractors((f - 1) * len(base))
        # faithful O(n^2)
        t0 = time.perf_counter()
        edges = SupersessionDetector().detect(corpus)
        dt = time.perf_counter() - t0
        # key-blocked (should give identical edges, near-linear)
        t1 = time.perf_counter()
        edges_b = SupersessionDetector(blocking=True).detect(corpus)
        dt_b = time.perf_counter() - t1

        detected = {e.older for e in edges}
        detected_b = {e.older for e in edges_b}
        tp = len(detected & gold)
        fp = len(detected - gold)
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / len(gold) if gold else 1.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        rows.append({"factor": f, "n_claims": len(corpus),
                     "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
                     "seconds_quadratic": round(dt, 2), "seconds_blocked": round(dt_b, 2),
                     "speedup": round(dt / dt_b, 1) if dt_b else None,
                     "blocked_identical": detected == detected_b})

    card = {"base_claims": len(base), "n_gold_superseded": len(gold), "scaling": rows}
    out = os.path.join(ROOT, "results", "scaling.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

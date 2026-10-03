"""U6: detection robustness to timestamp noise.

Recency is the detector's only orientation signal (an edge needs ``t_{c'} > t_c``), so the
natural question is how much date *quality* it needs. Gold supersession ``S*`` is fixed from
the clean annotations; we then corrupt only the timestamps the detector *sees* and measure
how span-level F1 degrades:

* ``drop``      -- a fraction of dates go missing (set to ``None``); such claims can no longer
  be the later member of a pair, so recall falls.
* ``randomize`` -- a fraction of dates are replaced by a random in-range year (wrong metadata /
  OCR errors), which mis-orients edges and costs precision as well as recall.

Averaged over seeds. Writes ``results/timestamp_noise_<name>.json``.

Usage: ``python -m experiments.run_timestamp_noise wikidata``.
"""

from __future__ import annotations

import json
import os
import random
import sys
from dataclasses import replace
from typing import List, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import gold_superseded  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRACS = [0.0, 0.1, 0.25, 0.5, 1.0]


def _f1(detected: Set[str], gold: Set[str]) -> float:
    tp = len(detected & gold)
    fp = len(detected - gold)
    fn = len(gold - detected)
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def _corrupt(claims: List[Claim], frac: float, mode: str, rng: random.Random) -> List[Claim]:
    dated = [i for i, c in enumerate(claims) if c.timestamp is not None]
    ts = [claims[i].timestamp for i in dated]
    lo, hi = (min(ts), max(ts)) if ts else (2000.0, 2020.0)
    k = int(round(frac * len(dated)))
    chosen = set(rng.sample(dated, k)) if k else set()
    out: List[Claim] = []
    for i, c in enumerate(claims):
        if i in chosen:
            if mode == "drop":
                out.append(replace(c, timestamp=None))
            else:  # randomize to a wrong in-range year
                out.append(replace(c, timestamp=float(round(rng.uniform(lo, hi)))))
        else:
            out.append(c)
    return out


def run(name: str, seeds: int = 5) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    gold = gold_superseded(claims)
    det = SupersessionDetector()
    card = {
        "dataset": name,
        "n_claims": len(claims),
        "n_gold_superseded": len(gold),
        "seeds": seeds,
        "clean_f1": round(_f1({e.older for e in det.detect(claims)}, gold), 4),
        "drop": {},
        "randomize": {},
    }
    for mode in ("drop", "randomize"):
        for frac in FRACS:
            reps = 1 if frac == 0.0 else seeds
            vals = []
            for s in range(reps):
                rng = random.Random(1000 * int(round(frac * 100)) + s)
                pert = _corrupt(claims, frac, mode, rng)
                detected = {e.older for e in det.detect(pert)}
                vals.append(_f1(detected, gold))
            card[mode][f"{frac:g}"] = round(sum(vals) / len(vals), 4)
    out = os.path.join(ROOT, "results", f"timestamp_noise_{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    datasets = sys.argv[1:] or ["wikidata"]
    for ds in datasets:
        print(json.dumps(run(ds), indent=2))

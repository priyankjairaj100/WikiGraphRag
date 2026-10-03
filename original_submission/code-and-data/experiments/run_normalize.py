"""Fig 3: raw-prose detection vs detection after LLM claim-normalization.

Runs the unchanged detector on the noisy prose claims, then again after a Qwen front-end
rewrites each into a canonical triple sentence. Expected: normalization restores the
structural exactness the matcher needs. Writes results/normalize_<name>.json.

Usage: ``python -m experiments.run_normalize wikidata_prose``.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.eval.detection import evaluate_detection  # noqa: E402
from wikigraphrag.parse.normalize import ClaimNormalizer  # noqa: E402
from wikigraphrag.readers.qwen import QwenReader  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(name: str) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    raw = evaluate_detection(claims)

    normalizer = ClaimNormalizer(QwenReader(max_new_tokens=64))
    normalized = normalizer.normalize_all(claims)
    norm = evaluate_detection(normalized)

    n_ok = sum(1 for c in normalized if c.meta.get("triple"))
    card = {
        "dataset": name,
        "n_claims": len(claims),
        "n_normalized_ok": n_ok,
        "raw": {k: raw[k] for k in ("precision", "recall", "f1")},
        "normalized": {k: norm[k] for k in ("precision", "recall", "f1")},
    }
    # save the normalized claims for inspection / reuse
    from wikigraphrag.data.io import save_claims

    save_claims(normalized, os.path.join(ROOT, "data", name + "_normalized", "claims.jsonl"))
    out = os.path.join(ROOT, "results", f"normalize_{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "wikidata_prose"
    print(json.dumps(run(dataset), indent=2))

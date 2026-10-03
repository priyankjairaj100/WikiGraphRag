"""Reproduce the SER/AEP retrieval effect (Table 4 / Lemma 1) on a built dataset.

Usage: ``python -m experiments.run_retrieval wikidata --k 5``. Writes
``results/retrieval_<name>.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.eval.retrieval import evaluate_retrieval, make_current_fact_queries  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(name: str, k: int) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    queries = make_current_fact_queries(claims)
    res = evaluate_retrieval(claims, queries, k=k)
    card = {"dataset": name, "k": k, "n_queries": len(queries), "conditions": res}
    out_dir = os.path.join(ROOT, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"retrieval_{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?", default="wikidata")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    print(json.dumps(run(args.dataset, args.k), indent=2))

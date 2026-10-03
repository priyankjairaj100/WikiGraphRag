"""Faithful-ish multi-hop retrieval sweep (Table 6): typed-PPR vs flat (and HippoRAG, opt-in).

For each question we retrieve over its own LongBench passage set and measure answer-presence
within a ~512-token budget -- the paper's metric. Flat (BM25+dense) and typed-PPR run with no
LLM; the faithful HippoRAG variant (LLM OpenIE + entity-graph PPR) is enabled with --hipporag
and is the slow part. Writes results/multihop_full.json.

Usage: python -m experiments.run_multihop_full [--n 50] [--hipporag]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.longbench import load  # noqa: E402
from wikigraphrag.eval.qa import normalize_answer  # noqa: E402
from wikigraphrag.retrieve.embed import Embedder  # noqa: E402
from wikigraphrag.retrieve.hybrid import HybridRetriever  # noqa: E402
from wikigraphrag.graphrag.ppr import TypedPPRRetriever  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS = ["hotpotqa", "2wikimqa", "musique"]
CHAR_BUDGET = 2000  # ~512 tokens


def _present(ranked_cids, by_cid, answers: List[str]) -> int:
    ctx, used = "", 0
    for cid in ranked_cids:
        t = by_cid[cid].text
        if used + len(t) > CHAR_BUDGET and ctx:
            break
        ctx += " " + t
        used += len(t)
    ctx_n = normalize_answer(ctx)
    return int(any(normalize_answer(a) in ctx_n for a in answers if a))


def run(n: int, hipporag: bool) -> dict:
    embedder = Embedder()
    reader = None
    if hipporag:
        from wikigraphrag.readers.qwen import QwenReader
        reader = QwenReader(max_new_tokens=128)
        from wikigraphrag.graphrag.hipporag import HippoRAG

    methods = ["flat", "typed_ppr"] + (["hipporag"] if hipporag else [])
    per_ds: Dict[str, Dict[str, float]] = {}
    macro: Dict[str, List[float]] = {m: [] for m in methods}

    for ds in DATASETS:
        examples = load(ds, n=n)
        hits = {m: 0 for m in methods}
        for question, passages, answers in examples:
            by_cid = {p.cid: p for p in passages}
            flat = HybridRetriever(passages, embedder=embedder)
            hits["flat"] += _present(flat.ranked_cids(question), by_cid, answers)
            ppr = TypedPPRRetriever(passages)
            hits["typed_ppr"] += _present(ppr.ranked_cids(question), by_cid, answers)
            if hipporag:
                hr = HippoRAG(passages, reader, embedder)
                hits["hipporag"] += _present(hr.ranked_cids(question), by_cid, answers)
        m = max(1, len(examples))
        per_ds[ds] = {meth: round(hits[meth] / m, 4) for meth in methods}
        for meth in methods:
            macro[meth].append(per_ds[ds][meth])

    card = {"n_per_dataset": n, "by_dataset": per_ds,
            "macro": {meth: round(sum(v) / len(v), 4) for meth, v in macro.items()}}
    fname = "multihop_hipporag.json" if hipporag else "multihop_full.json"
    out = os.path.join(ROOT, "results", fname)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--hipporag", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(args.n, args.hipporag), indent=2))

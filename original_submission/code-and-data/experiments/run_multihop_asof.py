"""Temporally-consistent multi-hop (as-of join) evaluation.

Composes two time-varying hops at the query time T:
  * ``as_of`` (ours): resolve hop 1 (A's parent) and hop 2 (that parent's CEO) both *as of T*
    over the detector's validity intervals.
  * ``current_only``: a naive current-value graph -- latest parent, latest CEO (ignores T).
  * ``gold``: same composition over gold intervals (oracle upper bound).
  * ``flat_retrieval_recall``: does top-5 similarity retrieval even surface the two T-valid
    gold claims a reader would need? (If not, flat RAG cannot compose regardless of reader.)

Writes ``results/multihop_asof.json``.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.graph import SupersessionEdge  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import _norm_value  # noqa: E402
from wikigraphrag.lifecycle.intervals import as_of, derive_timelines  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R1, R2 = "parent organization", "chief executive officer"


def _norm(s):
    return (s or "").strip().lower()


def _gold_edges(claims):
    by_key = defaultdict(list)
    for c in claims:
        if c.subject and c.relation and c.timestamp is not None:
            by_key[(_norm(c.subject), _norm(c.relation))].append(c)
    edges = []
    for cs in by_key.values():
        cs.sort(key=lambda c: c.timestamp)
        for i in range(len(cs) - 1):
            edges.append(SupersessionEdge(newer=cs[i + 1].cid, older=cs[i].cid,
                                          key=("", ""), confidence=1.0))
    return edges


def run(use_flat: bool = True, dataset: str = "multihop_asof") -> dict:
    d = os.path.join(ROOT, "data", dataset)
    claims = load_claims(os.path.join(d, "claims.jsonl"))
    with open(os.path.join(d, "queries.json"), encoding="utf-8") as fh:
        queries = json.load(fh)

    key_claims = defaultdict(list)
    for c in claims:
        key_claims[(_norm(c.subject), _norm(c.relation))].append(c)
    for k in key_claims:
        key_claims[k].sort(key=lambda c: c.timestamp)

    det_tl = derive_timelines(claims, SupersessionDetector().detect(claims))
    det_comp = {iv.cid: comp for comp, tl in det_tl.items() for iv in tl}
    gold_tl = derive_timelines(claims, _gold_edges(claims))
    gold_comp = {iv.cid: comp for comp, tl in gold_tl.items() for iv in tl}
    from wikigraphrag.detect.baselines import ExactKeyDetector
    exact_tl = derive_timelines(claims, ExactKeyDetector().detect(claims))
    exact_comp = {iv.cid: comp for comp, tl in exact_tl.items() for iv in tl}

    def tl_for(subject, relation, comp_map, tls):
        cs = key_claims.get((_norm(subject), _norm(relation)))
        return tls.get(comp_map.get(cs[0].cid)) if cs else None

    def asof_val(subject, relation, T, comp_map, tls):
        tl = tl_for(subject, relation, comp_map, tls)
        if not tl:
            return None
        iv = as_of(tl, T)
        return iv.value if iv else None

    def latest_val(subject, relation):
        cs = key_claims.get((_norm(subject), _norm(relation)))
        return cs[-1].value if cs else None

    def compose_asof(A, T, comp_map, tls):
        B = asof_val(A, R1, T, comp_map, tls)
        return asof_val(B, R2, T, comp_map, tls) if B else None

    def compose_current(A):
        B = latest_val(A, R1)
        return latest_val(B, R2) if B else None

    flat_recall = None
    if use_flat:
        from wikigraphrag.retrieve.hybrid import HybridRetriever
        retr = HybridRetriever(claims)
        ok = 0
        for q in queries:
            A, T, b1, ceo = q["company"], q["T"], q["gold_parent"], q["gold_answer"]
            h1 = next((c.cid for c in key_claims[(_norm(A), _norm(R1))]
                       if _norm(c.value) == _norm(b1)), None)
            h2 = next((c.cid for c in key_claims[(_norm(b1), _norm(R2))]
                       if _norm_value(c.value) == _norm_value(ceo)), None)
            top = set(retr.ranked_cids(q["question"])[:5])
            ok += int(h1 in top and h2 in top)
        flat_recall = round(ok / len(queries), 4)

    n = len(queries)
    hits = {"as_of": 0, "exact_key": 0, "current_only": 0, "gold": 0}
    for q in queries:
        A, T, gold = q["company"], q["T"], _norm_value(q["gold_answer"])
        hits["as_of"] += int(_norm_value(compose_asof(A, T, det_comp, det_tl) or "") == gold)
        hits["exact_key"] += int(_norm_value(compose_asof(A, T, exact_comp, exact_tl) or "") == gold)
        hits["gold"] += int(_norm_value(compose_asof(A, T, gold_comp, gold_tl) or "") == gold)
        hits["current_only"] += int(_norm_value(compose_current(A) or "") == gold)

    card = {
        "dataset": dataset,
        "n_queries": n,
        "composition_accuracy": {k: round(v / n, 4) for k, v in hits.items()},
        "flat_retrieval_recall_at5": flat_recall,
    }
    out = os.path.join(ROOT, "results", f"{dataset}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    use_flat = "--no-flat" not in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    ds = args[0] if args else "multihop_asof"
    print(json.dumps(run(use_flat=use_flat, dataset=ds), indent=2))

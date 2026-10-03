"""Evaluate the validity-interval extension: as-of retrieval on historical questions.

For every past value period we probe a time T inside it and ask for the value valid then.
As-of retrieval (derived from the detector's edges) should recover it; a current-only reader
(what plain lifecycle filtering gives) always returns the latest value and so fails on history.
This shows the extension is a strict generalization at no extra supervision.

Usage: ``python -m experiments.run_asof wikidata``. Writes results/asof_<name>.json.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.detect.baselines import ExactKeyDetector, LLMKeyDetector  # noqa: E402
from wikigraphrag.eval.detection import _norm_value  # noqa: E402
from wikigraphrag.lifecycle.intervals import as_of, derive_timelines  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gold_timelines(claims: List[Claim]) -> Dict[object, List[Claim]]:
    by_key: Dict[object, List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)
    return {k: sorted(v, key=lambda c: c.timestamp) for k, v in by_key.items()}


def run(name: str, use_llm: bool = False) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    edges = SupersessionDetector().detect(claims)
    det_timelines = derive_timelines(claims, edges)
    cid_to_comp = {iv.cid: comp for comp, tl in det_timelines.items() for iv in tl}
    # Text-driven exact-key temporal-KG baseline (structured-KG assumption, no fuzzy matching).
    exact_timelines = derive_timelines(claims, ExactKeyDetector().detect(claims))
    exact_comp = {iv.cid: comp for comp, tl in exact_timelines.items() for iv in tl}
    interval_methods = {
        "as_of": (det_timelines, cid_to_comp),
        "exact_key": (exact_timelines, exact_comp),
    }
    if use_llm:
        from wikigraphrag.readers.openai_reader import OpenAIReader
        llm_tl = derive_timelines(claims, LLMKeyDetector(OpenAIReader()).detect(claims))
        interval_methods["llm_key"] = (llm_tl, {iv.cid: comp for comp, tl in llm_tl.items() for iv in tl})
    gold = _gold_timelines(claims)

    # build probes at the midpoint of every value period
    hist_probes: List[Tuple[object, float, str]] = []
    curr_probes: List[Tuple[object, float, str]] = []
    for key, group in gold.items():
        if len(group) < 2:
            continue
        # distinct-value change points
        points = []
        for c in group:
            if not points or _norm_value(points[-1].value) != _norm_value(c.value):
                points.append(c)
        for i, c in enumerate(points):
            nxt = points[i + 1].timestamp if i + 1 < len(points) else None
            if nxt is None:
                curr_probes.append((key, c.timestamp + 1.0, _norm_value(c.value)))
            else:
                hist_probes.append((key, (c.timestamp + nxt) / 2.0, _norm_value(c.value)))

    def score(probes, method: str) -> float:
        if not probes:
            return float("nan")
        hits = 0
        for key, t, gold_val in probes:
            latest = gold[key][-1]
            if method == "current_only":
                pred = _norm_value(latest.value)
            else:  # interval methods (as_of ours / exact_key / llm_key): value in force at t
                tls, comp = interval_methods[method]
                cid0 = gold[key][0].cid
                tl = tls.get(comp.get(cid0, ""), [])
                iv = as_of(tl, t)
                pred = _norm_value(iv.value) if iv and iv.value else ""
            hits += int(pred == gold_val)
        return round(hits / len(probes), 4)

    def block(probes):
        d = {
            "as_of": score(probes, "as_of"),
            "exact_key": score(probes, "exact_key"),
            "current_only": score(probes, "current_only"),
        }
        if use_llm:
            d["llm_key"] = score(probes, "llm_key")
        return d

    card = {
        "dataset": name,
        "n_historical_probes": len(hist_probes),
        "n_current_probes": len(curr_probes),
        "historical_accuracy": block(hist_probes),
        "current_accuracy": block(curr_probes),
    }
    out = os.path.join(ROOT, "results", f"asof_{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    dataset = argv[0] if argv else "wikidata"
    print(json.dumps(run(dataset, use_llm="--llm" in sys.argv), indent=2))

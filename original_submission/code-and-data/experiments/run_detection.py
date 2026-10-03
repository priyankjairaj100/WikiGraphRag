"""Run supersession detection on a built dataset and write a JSON result card.

Writes to ``results/detection_<name>.json`` (and prints) so numbers survive even when the
terminal swallows stdout. Usage: ``python -m experiments.run_detection wikidata``.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import evaluate_detection  # noqa: E402
from wikigraphrag.eval.stats import cluster_bootstrap_f1  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(name: str) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    res = evaluate_detection(claims)
    edges = res.pop("edges")
    res["n_edges"] = len(edges)
    res["f1_ci95"] = cluster_bootstrap_f1(claims, SupersessionDetector())

    # attach readable text for any error spans, to make debugging a glance
    by_cid = {c.cid: c for c in claims}
    res["false_positive_spans"] = [by_cid[c].text for c in res["false_positives"][:25]]
    res["false_negative_spans"] = [by_cid[c].text for c in res["false_negatives"][:25]]

    out_dir = os.path.join(ROOT, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"detection_{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    return res


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "wikidata"
    r = run(dataset)
    print(json.dumps({k: r[k] for k in ("precision", "recall", "f1", "tp", "fp", "fn",
                                         "n_claims", "n_gold_superseded")}, indent=2))

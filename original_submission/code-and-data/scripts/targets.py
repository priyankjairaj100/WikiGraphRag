"""Fidelity scorecard: my reconstruction's numbers vs the paper's published targets.
Run after any fix to see progress. Usage: python scripts/targets.py [detection|ablation|all]"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wikigraphrag.data.io import load_claims
from wikigraphrag.eval.detection import evaluate_detection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Paper's published F1 targets.
DET_TARGET = {"freshrag": 1.000, "realworld": 1.000, "wikidata": 1.000,
              "templama": 0.976, "wikidata_prose": 0.998}
# Paper Table 3 ablation F1 targets {full, -subject, -value, -recency, -all}.
ABL_TARGET = {
    "freshrag": [1.000, 1.000, 1.000, 0.256, 0.270],
    "wikidata": [1.000, 0.844, 0.914, 0.843, 0.725],
    "templama": [0.976, 0.985, 0.988, 0.384, 0.395],
    "wikidata_prose": [0.998, 0.413, 0.507, 0.869, 0.376],
}


def _det(d):
    return evaluate_detection(load_claims(os.path.join(ROOT, "data", d, "claims.jsonl")))


def detection():
    print("== DETECTION F1 (mine vs paper) ==")
    for d, t in DET_TARGET.items():
        r = _det(d)
        flag = "OK" if abs(r["f1"] - t) < 0.01 else f"off {r['f1']-t:+.3f}"
        print(f"  {d:16} mine={r['f1']:.4f}  P={r['precision']:.3f} R={r['recall']:.3f} "
              f"fp={r['fp']} fn={r['fn']}  target={t:.3f}  [{flag}]")


def ablation():
    from wikigraphrag.detect import SupersessionDetector
    from wikigraphrag.eval.detection import gold_superseded

    def f1_for(claims, **kw):
        import statistics
        seeds = [0, 1, 2, 3, 4] if kw.get("randomize_time") else [0]
        vals = []
        for s in seeds:
            det = SupersessionDetector(seed=s, **kw)
            detected = {e.older for e in det.detect(claims)}
            g = gold_superseded(claims)
            tp = len(detected & g); fp = len(detected - g); fn = len(g - detected)
            p = tp / (tp + fp) if (tp + fp) else 1.0
            r = tp / (tp + fn) if (tp + fn) else 1.0
            vals.append(2 * p * r / (p + r) if (p + r) else 0.0)
        return statistics.mean(vals)

    print("\n== ABLATION F1 {full,-subj,-value,-recency,-all} (mine vs paper) ==")
    for d, tgt in ABL_TARGET.items():
        claims = load_claims(os.path.join(ROOT, "data", d, "claims.jsonl"))
        mine = [
            f1_for(claims),
            f1_for(claims, use_subject=False),
            f1_for(claims, use_value=False),
            f1_for(claims, randomize_time=True),
            f1_for(claims, use_subject=False, use_value=False, randomize_time=True),
        ]
        print(f"  {d:16} mine={[round(x,3) for x in mine]}")
        print(f"  {'':16} papr={tgt}")


if __name__ == "__main__":
    detection()
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        ablation()

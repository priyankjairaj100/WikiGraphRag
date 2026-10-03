"""Diagnostic: why does the matcher miss/false-fire on a dataset? Prints FN and FP pairs
with their extracted features and which gate fails. Usage: python scripts/diag_matcher.py wikidata_prose"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import defaultdict
from wikigraphrag.data.io import load_claims
from wikigraphrag.detect import SupersessionDetector
from wikigraphrag.detect.features import extract_features
from wikigraphrag.detect.signals import same_subject_relation, changed_value
from wikigraphrag.eval.detection import gold_superseded, _norm_value

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(name: str, limit: int = 8) -> None:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    by_cid = {c.cid: c for c in claims}
    feats = {c.cid: extract_features(c) for c in claims}
    gold = gold_superseded(claims)
    detected = {e.older for e in SupersessionDetector().detect(claims)}
    fns = sorted(gold - detected)
    fps = sorted(detected - gold)
    print(f"== {name}: gold={len(gold)} detected={len(detected)} FN={len(fns)} FP={len(fps)} ==")

    # group by gold key to find the intended superseder
    by_key = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)

    print("\n--- FALSE NEGATIVES (missed true supersessions) ---")
    for cid in fns[:limit]:
        c = by_cid[cid]
        # find the actual later same-key different-value claim
        laters = [o for o in by_key[c.gold_key]
                  if o.timestamp > c.timestamp and _norm_value(o.value) != _norm_value(c.value)]
        print(f"\nOLDER {cid}\n  text: {c.text!r}")
        print(f"  frame={set(feats[cid].frame)} phrases={set(feats[cid].phrases)} pnc={feats[cid].proper_noun_count}")
        for o in laters[:1]:
            fo, fc = feats[o.cid], feats[cid]
            ssr = same_subject_relation(fo, fc)
            cv = changed_value(fo, fc)
            print(f"NEWER {o.cid}\n  text: {o.text!r}")
            print(f"  frame={set(fo.frame)} phrases={set(fo.phrases)} pnc={fo.proper_noun_count}")
            print(f"  -> same_subject_relation={ssr}  changed_value={cv}  (need both True)")
            print(f"  shared_phrases={set(fo.phrases) & set(fc.phrases)}")

    print("\n--- FALSE POSITIVES (spurious supersessions) ---")
    for cid in fps[:limit]:
        c = by_cid[cid]
        print(f"\n{cid}: {c.text!r}  key={c.gold_key}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "wikidata_prose",
         int(sys.argv[2]) if len(sys.argv) > 2 else 8)

"""Build (and verify) the SHA-256 reproducibility bundle -- the artifact that was lost.

Hashes every dataset (claims.jsonl) and every result card, extracts the headline numbers,
and writes ``results/MANIFEST.json`` with a claim-to-file map. ``--verify`` re-hashes and
reports any drift, so a reviewer can confirm the numbers match the files bit-for-bit.

Usage:
  python -m experiments.build_bundle            # build the manifest
  python -m experiments.build_bundle --verify   # verify against an existing manifest
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from typing import Dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
MANIFEST = os.path.join(RESULTS, "MANIFEST.json")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


def _headlines() -> Dict[str, object]:
    """Pull the key numbers out of the result cards for an at-a-glance summary."""
    out: Dict[str, object] = {}
    det: Dict[str, object] = {}
    for f in sorted(glob.glob(os.path.join(RESULTS, "detection_*.json"))):
        name = os.path.basename(f)[len("detection_"):-len(".json")]
        d = json.load(open(f, encoding="utf-8"))
        det[name] = {"precision": d["precision"], "recall": d["recall"], "f1": d["f1"],
                     "n_claims": d["n_claims"], "ci95": d.get("f1_ci95", {})}
    out["detection"] = det
    for tag in (
        "retrieval_wikidata",
        "retrieval_templama",
        "qa_wikidata_extractive",
        "qa_wikidata_qwen",
        "qa_templama_extractive",
        "qa_templama_qwen",
        "temporal_rag_baselines_extractive",
        "positional_compare",
        "pooled_wikidata_freshrag_realworld_templama",
    ):
        p = os.path.join(RESULTS, tag + ".json")
        if os.path.exists(p):
            out[tag] = json.load(open(p, encoding="utf-8"))
    return out


def build() -> dict:
    files: Dict[str, str] = {}
    claim_to_file: Dict[str, dict] = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "*", "claims.jsonl"))):
        name = os.path.basename(os.path.dirname(f))
        n = sum(1 for _ in open(f, encoding="utf-8"))
        claim_to_file[name] = {"path": _rel(f), "sha256": _sha256(f), "n_claims": n}
    for f in sorted(glob.glob(os.path.join(RESULTS, "*.json"))):
        if os.path.basename(f) == "MANIFEST.json":
            continue
        files[_rel(f)] = _sha256(f)

    manifest = {
        "project": "wikigraphrag-reproduction",
        "datasets": claim_to_file,
        "result_files": files,
        "headline_numbers": _headlines(),
    }
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def verify() -> int:
    if not os.path.exists(MANIFEST):
        print("no manifest; run build first")
        return 1
    man = json.load(open(MANIFEST, encoding="utf-8"))
    drift = 0
    for name, info in man["datasets"].items():
        cur = _sha256(os.path.join(ROOT, info["path"]))
        status = "OK" if cur == info["sha256"] else "DRIFT"
        if status == "DRIFT":
            drift += 1
        print(f"{status}  dataset {name}")
    for rel, want in man["result_files"].items():
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            print(f"MISSING {rel}")
            drift += 1
            continue
        if _sha256(p) != want:
            print(f"DRIFT  {rel}")
            drift += 1
    print(f"\n{'ALL VERIFIED' if drift == 0 else str(drift) + ' MISMATCH(ES)'}")
    return 1 if drift else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.verify:
        raise SystemExit(verify())
    m = build()
    print(json.dumps({"datasets": list(m["datasets"]), "n_result_files": len(m["result_files"])},
                     indent=2))

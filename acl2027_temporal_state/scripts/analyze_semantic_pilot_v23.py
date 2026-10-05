#!/usr/bin/env python3
"""Analyze a frozen, externally reviewed semantic pilot; never run a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from temporal_state.semantic_gate_v23 import analyze_pilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve the previous receipt and choose a new version")
    raw = args.bundle.read_bytes()
    result = analyze_pilot(json.loads(raw))
    result["bundle_sha256"] = hashlib.sha256(raw).hexdigest()
    result["analyzer_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["module_sha256"] = hashlib.sha256((Path(__file__).resolve().parents[1] / "src/temporal_state/semantic_gate_v23.py").read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"receipt": str(args.output), "counts": result["counts"],
                      "class_recall": result["class_recall"], "selective_risk": result["selective_risk"]}))


if __name__ == "__main__":
    main()

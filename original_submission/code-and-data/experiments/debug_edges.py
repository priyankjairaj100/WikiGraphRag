"""Debug: for each detection false positive, show the newer claim that produced the edge."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import gold_superseded  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(name: str, limit: int = 12) -> None:
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    by_cid = {c.cid: c for c in claims}
    edges = SupersessionDetector().detect(claims)
    gold = gold_superseded(claims)
    fp = [e for e in edges if e.older not in gold]
    lines = [f"total_edges={len(edges)} fp_edges={len(fp)}"]
    for e in fp[:limit]:
        older, newer = by_cid[e.older], by_cid[e.newer]
        lines.append("")
        lines.append(f"OLDER (fp): [{older.timestamp:.2f}] {older.text}")
        lines.append(f"NEWER     : [{newer.timestamp:.2f}] {newer.text}")
        lines.append(f"edge.key={e.key}")
    out = os.path.join(ROOT, "results", f"debug_fp_{name}.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "freshrag")

"""Build a supersession-detection benchmark from the third-party TempLAMA (Dhingra et al. 2022).

TempLAMA gives, per (subject, relation), a yearly answer that changes over time. We download
the ``Yova/templama`` mirror (the official generator needs Wikidata SPARQL, which is under an
outage rate-limit), collapse consecutive equal-answer years into distinct value change-points,
and render each as a claim by filling the query's ``_X_`` slot. This is genuine third-party
data whose labels we did not author -- the honest detection test.

Run: ``python -m wikigraphrag.data.templama``.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, List, Tuple

from ..core.claim import Claim, parse_timestamp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "templama")

REPO = "Yova/templama"
SPLIT_FILE = "test_with_aliases.json"
MAX_CASES = 300  # match the paper's scale


def _load_rows() -> List[dict]:
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(REPO, SPLIT_FILE, repo_type="dataset")
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _answer_name(row: dict) -> Tuple[str, str]:
    """(display_name, wikidata_id) of the row's answer, or ('', '') if missing."""
    ans = row.get("answer") or []
    if not ans:
        return "", ""
    a = ans[0]
    name = a.get("original_name") or a.get("name") or [""]
    disp = name[0] if isinstance(name, list) else name
    return disp, a.get("wikidata_id", "")


def build(write: bool = True) -> List[Claim]:
    rows = _load_rows()
    by_key: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        rid = r.get("id", "")
        parts = rid.rsplit("_", 1)  # "Q313381_P54_2010" -> ("Q313381_P54", "2010")
        if len(parts) != 2:
            continue
        by_key[parts[0]].append(r)

    claims: List[Claim] = []
    n_cases = 0
    for key in sorted(by_key):
        group = sorted(by_key[key], key=lambda r: r.get("date", ""))
        # collapse consecutive equal answers into distinct value change-points
        changepoints: List[Tuple[str, str, str]] = []  # (year, name, qid)
        last_qid = None
        for r in group:
            name, qid = _answer_name(r)
            if not name or qid == last_qid:
                continue
            changepoints.append((r.get("date", ""), name, qid))
            last_qid = qid
        if len(changepoints) < 2:
            continue  # need a real change to be a chronology case
        n_cases += 1
        if n_cases > MAX_CASES:
            break

        subject_qid, relation_pid = key.rsplit("_", 1)
        query = group[0].get("query", "_X_")
        for i, (year, name, qid) in enumerate(changepoints):
            text = query.replace("_X_", name).strip()
            if not text.endswith((".", "!", "?")):
                text += "."
            claims.append(
                Claim(
                    cid=f"tl:{key}:{year}:{qid}",
                    text=text,
                    timestamp=parse_timestamp(year),
                    doc_id=f"tl:{key}:{year}",
                    subject=subject_qid,
                    relation=relation_pid,
                    value=name,
                    meta={"key": key, "query": query, "year": year, "answer_qid": qid},
                )
            )

    if write:
        from .io import save_claims

        save_claims(claims, os.path.join(OUT_DIR, "claims.jsonl"))
        _write_meta(claims, os.path.join(OUT_DIR, "meta.json"))
    return claims


def _write_meta(claims: List[Claim], path: str) -> None:
    from ..eval.detection import gold_superseded

    sup = gold_superseded(claims)
    keys = {c.gold_key for c in claims}
    meta = {
        "name": "templama", "source": REPO, "split": SPLIT_FILE,
        "claims": len(claims), "cases": len(keys),
        "superseded": len(sup), "active": len(claims) - len(sup),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


if __name__ == "__main__":
    cs = build(write=True)
    from ..eval.detection import gold_superseded

    print(json.dumps({"claims": len(cs), "cases": len({c.gold_key for c in cs}),
                      "superseded": len(gold_superseded(cs))}, indent=2))

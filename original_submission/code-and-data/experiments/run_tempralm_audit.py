"""Audit TempRALM topicality under the headline current-answer protocol.

Reports how many same-key claims survive in the top five, whether the active claim is
present, and whether the first claim has the requested key/value. This isolates retrieval
behavior without invoking a reader.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

from wikigraphrag.core.claim import Claim
from wikigraphrag.data.io import load_claims
from wikigraphrag.retrieve.hybrid import HybridRetriever
from wikigraphrag.retrieve.temporal import ranked_cids, tempralm_scores

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_QUESTION = {
    "head of government": "Who is the current head of government of {subject}?",
    "chief executive officer": "Who is the current CEO of {subject}?",
}


def _groups(claims: List[Claim]) -> Dict[Tuple[str, str], List[Claim]]:
    groups: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
    for claim in claims:
        if claim.gold_key is not None and claim.timestamp is not None:
            groups[claim.gold_key].append(claim)
    return groups


def _question(group: List[Claim]) -> str:
    template = group[0].meta.get("query") if group[0].meta else None
    if template and "_X_" in template:
        statement = template.replace("_X_", "____").strip()
        return f"As of the most recent date, what single value fills the blank? {statement}"
    relation = group[0].relation or "value"
    if relation in _QUESTION:
        return _QUESTION[relation].format(subject=group[0].subject)
    return f"What is the current {relation} of {group[0].subject}?"


def _measure(order: List[str], by_cid: Dict[str, Claim], key: Tuple[str, str], active: str) -> dict:
    top_five = order[:5]
    return {
        "mean_same_key_claims_in_top5": sum(by_cid[cid].gold_key == key for cid in top_five),
        "active_claim_in_top5": float(active in top_five),
        "top1_has_requested_key": float(by_cid[top_five[0]].gold_key == key),
        "top1_is_active_claim": float(top_five[0] == active),
    }


def run() -> dict:
    output = {}
    for dataset in ("wikidata", "templama"):
        claims = load_claims(os.path.join(ROOT, "data", dataset, "claims.jsonl"))
        retriever = HybridRetriever(claims)
        by_cid = {claim.cid: claim for claim in claims}
        timestamps = np.asarray([claim.timestamp or 0.0 for claim in claims], dtype=np.float64)
        query_time = float(timestamps.max())
        records = {"flat": [], "tempralm": []}

        for key, group in _groups(claims).items():
            if len(group) < 2:
                continue
            active = max(group, key=lambda claim: claim.timestamp).cid
            question = _question(group)
            flat = retriever.ranked_cids(question)
            temporal = ranked_cids(
                retriever.cids,
                tempralm_scores(retriever.dense_scores(question), timestamps, query_time),
            )
            records["flat"].append(_measure(flat, by_cid, key, active))
            records["tempralm"].append(_measure(temporal, by_cid, key, active))

        output[dataset] = {"n_questions": len(records["flat"]), "k": 5}
        for condition, rows in records.items():
            output[dataset][condition] = {
                metric: round(sum(row[metric] for row in rows) / len(rows), 4)
                for metric in rows[0]
            }

    path = os.path.join(ROOT, "results", "tempralm_topicality_audit.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2)
    return output


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
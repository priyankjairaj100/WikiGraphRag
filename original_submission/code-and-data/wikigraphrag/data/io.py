"""Load/save claims as JSONL so datasets are reproducible and diff-friendly."""

from __future__ import annotations

import json
import os
from typing import Iterable, List

from ..core.claim import Claim


def save_claims(claims: Iterable[Claim], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for c in claims:
            fh.write(json.dumps(_to_dict(c), ensure_ascii=False) + "\n")


def load_claims(path: str) -> List[Claim]:
    out: List[Claim] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(_from_dict(json.loads(line)))
    return out


def _to_dict(c: Claim) -> dict:
    return {
        "cid": c.cid,
        "text": c.text,
        "timestamp": c.timestamp,
        "doc_id": c.doc_id,
        "subject": c.subject,
        "relation": c.relation,
        "value": c.value,
        "valid_time": c.valid_time,
        "span": list(c.span) if c.span else None,
        "meta": dict(c.meta),
    }


def _from_dict(d: dict) -> Claim:
    span = d.get("span")
    return Claim(
        cid=d["cid"],
        text=d["text"],
        timestamp=d.get("timestamp"),
        doc_id=d.get("doc_id", "doc"),
        subject=d.get("subject"),
        relation=d.get("relation"),
        value=d.get("value"),
        valid_time=d.get("valid_time"),
        span=tuple(span) if span else None,
        meta=d.get("meta", {}),
    )

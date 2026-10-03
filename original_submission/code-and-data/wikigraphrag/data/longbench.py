"""Loader for the LongBench multi-hop configs (HotpotQA / 2WikiMQA / MuSiQue).

Each row is a question, a ``context`` of "Passage N: ..." chunks, and gold ``answers``. We split
the context into passages and wrap each as a lightweight :class:`Claim` (no timestamp) so the
existing hybrid and typed-PPR retrievers work over them unchanged. Retrieval is per question,
over that question's own passage set -- the standard multi-hop setup.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Tuple

from ..core.claim import Claim

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DIR = os.path.join(ROOT, "data", "longbench")

_SPLIT = re.compile(r"Passage\s+\d+:\s*", re.IGNORECASE)

Example = Tuple[str, List[Claim], List[str]]  # (question, passages, answers)


def load(dataset: str, n: int = 50) -> List[Example]:
    path = os.path.join(DIR, f"{dataset}.jsonl")
    out: List[Example] = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if len(out) >= n:
                break
            row = json.loads(line)
            passages = [p.strip() for p in _SPLIT.split(row.get("context", "")) if p.strip()]
            if not passages:
                continue
            claims = [
                Claim(cid=f"{dataset}:{i}:{j}", text=p[:1200], timestamp=None,
                      doc_id=f"{dataset}:{i}")
                for j, p in enumerate(passages)
            ]
            out.append((row["input"], claims, row.get("answers", [])))
    return out

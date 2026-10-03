"""History-length sweep (Table 3): isolate the which-value mechanism as m grows.

Truncate each chronology to its latest m distinct values, then compare, at each m:
* flat AEP  -- predicted 1/m by Lemma 1 (all m values equally relevant to a value-agnostic query);
* reader EM under flat / date-prompt / date-rerank / lifecycle contexts.

The point: date tricks reorder or re-weight stale values but cannot remove them, so their EM
decays as m grows, while lifecycle (which filters stale context) holds. Uses the local
Qwen reader; generations are cached.

Usage: ``python -m experiments.run_history_sweep``.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.eval.detection import _norm_value  # noqa: E402
from wikigraphrag.eval.qa import exact_match  # noqa: E402
from wikigraphrag.readers.base import build_prompt_dated, build_prompt_neutral  # noqa: E402
from wikigraphrag.readers.qwen import QwenReader  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MS = [2, 3, 4, 5]

_Q = {
    "head of government": "Who is the current head of government of {s}?",
    "chief executive officer": "Who is the current CEO of {s}?",
    "president": "Who is the current president of {s}?",
    "prime minister": "Who is the current prime minister of {s}?",
}


def _changepoints(group: List[Claim]) -> List[Claim]:
    group = sorted(group, key=lambda c: c.timestamp)
    pts: List[Claim] = []
    for c in group:
        if not pts or _norm_value(pts[-1].value) != _norm_value(c.value):
            pts.append(c)
    return pts


def run() -> dict:
    claims = load_claims(os.path.join(ROOT, "data", "wikidata", "claims.jsonl"))
    by_key: Dict[object, List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)
    chrono = {k: _changepoints(v) for k, v in by_key.items()}
    chrono = {k: v for k, v in chrono.items() if v and v[0].relation in _Q and len(v) >= 2}

    reader = QwenReader(max_new_tokens=24)
    rows: Dict[int, dict] = {}
    for m in MS:
        keys_m = [(k, v[-m:]) for k, v in chrono.items() if len(v) >= m]
        if not keys_m:
            continue
        aep = 1.0 / m  # all m equally relevant, exactly one active -> Lemma 1
        em = {"flat": 0, "date_prompt": 0, "date_rerank": 0, "lifecycle": 0}
        for _key, seq in keys_m:
            active = seq[-1]
            gold = active.value
            q = _Q[seq[0].relation].format(s=seq[0].subject)
            oldest_first = list(seq)                      # arbitrary wrt relevance
            newest_first = list(reversed(seq))
            ctx = {
                "flat": build_prompt_neutral(q, oldest_first),          # no signal
                "date_prompt": build_prompt_dated(q, oldest_first),      # trust-newest instruction
                "date_rerank": build_prompt_neutral(q, newest_first),    # newest-first order
                "lifecycle": build_prompt_neutral(q, [active]),          # stale dropped
            }
            for cond, prompt in ctx.items():
                ans = reader.generate(prompt, max_new_tokens=24)
                em[cond] += int(exact_match(ans, gold))
        n = len(keys_m)
        rows[m] = {"n_keys": n, "flat_AEP": round(aep, 4),
                   **{f"EM_{k}": round(v / n, 4) for k, v in em.items()}}

    card = {"dataset": "wikidata", "budget": "full-context", "by_m": rows}
    out = os.path.join(ROOT, "results", "history_sweep.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    return card


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

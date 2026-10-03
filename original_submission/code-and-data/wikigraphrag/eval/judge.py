"""LLM-judge baseline vs the zero-LLM heuristic on identical span pairs (Table 2).

We build a balanced set of labelled claim pairs -- true supersessions (same key, later,
different value) and hard negatives (different subject but same relation, plus same-value
restatements) -- and score both a prompted judge and the heuristic on the *same* pairs. This
mirrors the paper's "identical span pairs" comparison and is tractable for a local model.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.claim import Claim
from ..detect.features import extract_features
from ..detect.signals import changed_value, same_subject_relation
from .detection import _norm_value

Pair = Tuple[Claim, Claim, bool]  # (older, newer, is_supersession)


def build_pairs(claims: Sequence[Claim], n_neg: int = 150, seed: int = 0) -> List[Pair]:
    rng = random.Random(seed)
    by_key: Dict[object, List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)

    positives: List[Pair] = []
    for group in by_key.values():
        for a in group:
            for b in group:
                if b.timestamp > a.timestamp and _norm_value(b.value) != _norm_value(a.value):
                    positives.append((a, b, True))

    # hard negatives: same relation, different subject (should NOT be a supersession)
    by_rel: Dict[Optional[str], List[Claim]] = defaultdict(list)
    for c in claims:
        by_rel[c.relation].append(c)
    negatives: List[Pair] = []
    attempts = 0
    while len(negatives) < n_neg and attempts < n_neg * 50:
        attempts += 1
        rel = rng.choice(list(by_rel))
        pool = by_rel[rel]
        if len(pool) < 2:
            continue
        a, b = rng.sample(pool, 2)
        if a.gold_key == b.gold_key:
            continue
        older, newer = (a, b) if (a.timestamp or 0) < (b.timestamp or 0) else (b, a)
        negatives.append((older, newer, False))

    rng.shuffle(positives)
    pairs = positives[:n_neg] + negatives  # balanced: ~n_neg positives + n_neg negatives
    rng.shuffle(pairs)
    return pairs


def heuristic_labels(pairs: Sequence[Pair]) -> List[bool]:
    """The heuristic's yes/no for each pair: does 'newer' supersede 'older'?"""
    out: List[bool] = []
    for older, newer, _ in pairs:
        fa, fb = extract_features(older), extract_features(newer)
        out.append(same_subject_relation(fb, fa) and changed_value(fb, fa))
    return out


_JUDGE_PROMPT = (
    "A ({ta}): {a}\n"
    "B ({tb}): {b}\n\n"
    "Does statement B make statement A out of date? Answer yes or no."
)

_YES = re.compile(r"\byes\b", re.IGNORECASE)


def judge_labels(pairs: Sequence[Pair], reader) -> List[bool]:
    out: List[bool] = []
    for older, newer, _ in pairs:
        prompt = _JUDGE_PROMPT.format(
            ta=_fmt_t(older), a=older.text, tb=_fmt_t(newer), b=newer.text
        )
        resp = reader.generate(prompt, max_new_tokens=8)
        out.append(bool(_YES.search(resp)))
    return out


def _fmt_t(c: Claim) -> str:
    return str(int(c.timestamp)) if c.timestamp else "n/a"


def prf(pred: Sequence[bool], gold: Sequence[bool]) -> Dict[str, float]:
    tp = sum(1 for p, g in zip(pred, gold) if p and g)
    fp = sum(1 for p, g in zip(pred, gold) if p and not g)
    fn = sum(1 for p, g in zip(pred, gold) if not p and g)
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}

"""Bootstrap statistics: cluster CIs and paired tests (the paper's 2,000-resample scheme).

Detection CIs are *cluster* bootstraps over cases (a case = one gold key / chronology): we
resample cases with replacement and recompute the metric, so the interval respects the fact
that spans within a case are correlated. Paired comparisons (heuristic vs a competitor) use a
paired bootstrap with a two-sided bootstrap p-value.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Callable, Dict, List, Sequence, Tuple

from ..core.claim import Claim
from .detection import gold_superseded


def _f1_from_counts(tp: int, fp: int, fn: int) -> float:
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def _percentile(xs: Sequence[float], q: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    idx = min(len(ys) - 1, max(0, int(round(q * (len(ys) - 1)))))
    return ys[idx]


def detection_case_counts(claims: List[Claim], detector) -> List[Tuple[int, int, int]]:
    """Per-case ``(tp, fp, fn)`` under *pooled* detection (each claim attributed to its key)."""
    detected = {e.older for e in detector.detect(claims)}
    gold = gold_superseded(claims)
    groups: Dict[object, List[Claim]] = defaultdict(list)
    for c in claims:
        groups[c.gold_key].append(c)
    out: List[Tuple[int, int, int]] = []
    for group in groups.values():
        cids = {c.cid for c in group}
        tp = len(detected & gold & cids)
        fp = len((detected - gold) & cids)
        fn = len((gold - detected) & cids)
        out.append((tp, fp, fn))
    return out


def cluster_bootstrap_f1(
    claims: List[Claim], detector, n_resamples: int = 2000, seed: int = 0
) -> Dict[str, float]:
    """Point F1 plus a 95% cluster-bootstrap CI over cases."""
    counts = detection_case_counts(claims, detector)
    tp = sum(c[0] for c in counts)
    fp = sum(c[1] for c in counts)
    fn = sum(c[2] for c in counts)
    point = _f1_from_counts(tp, fp, fn)

    rng = random.Random(seed)
    k = len(counts)
    boots: List[float] = []
    for _ in range(n_resamples):
        stp = sfp = sfn = 0
        for _ in range(k):
            a, b, c = counts[rng.randrange(k)]
            stp += a
            sfp += b
            sfn += c
        boots.append(_f1_from_counts(stp, sfp, sfn))
    return {
        "f1": round(point, 4),
        "ci95_low": round(_percentile(boots, 0.025), 4),
        "ci95_high": round(_percentile(boots, 0.975), 4),
        "n_cases": k,
    }


def paired_bootstrap(
    per_item_a: Sequence[float],
    per_item_b: Sequence[float],
    n_resamples: int = 2000,
    seed: int = 0,
) -> Dict[str, float]:
    """Mean difference (a - b) with 95% CI and a two-sided bootstrap p-value.

    ``per_item_*`` are aligned per-item scores (e.g. per-question exact match). Used for the
    "beats the LLM judge" and "lifecycle vs flat" comparisons.
    """
    assert len(per_item_a) == len(per_item_b)
    diffs = [a - b for a, b in zip(per_item_a, per_item_b)]
    n = len(diffs)
    obs = sum(diffs) / n if n else 0.0
    rng = random.Random(seed)
    boots: List[float] = []
    for _ in range(n_resamples):
        s = sum(diffs[rng.randrange(n)] for _ in range(n))
        boots.append(s / n)
    # two-sided p-value: fraction of resamples on the opposite side of 0 from the observation
    ge = sum(1 for d in boots if d <= 0) if obs > 0 else sum(1 for d in boots if d >= 0)
    p = 2.0 * ge / len(boots) if boots else 1.0
    return {
        "mean_diff": round(obs, 4),
        "ci95_low": round(_percentile(boots, 0.025), 4),
        "ci95_high": round(_percentile(boots, 0.975), 4),
        "p_value": round(min(1.0, p), 4),
        "n": n,
    }

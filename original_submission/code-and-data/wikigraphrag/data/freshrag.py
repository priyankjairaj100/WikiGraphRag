"""Synthesise FreshRAG-Bench: dated policy circulars whose numeric thresholds change.

Each *scheme* has a metric (an income threshold, a fee, an age limit, ...) that takes a
sequence of dated values; every older value is superseded by the next. We also emit a
fraction of *restatement* circulars (the same value re-announced later) -- these must NOT be
flagged, which is precisely what the changed-value gate is for. Dates are rendered as
"In {Month} {Year}" so no date digit leaks into the numeric value.

Deterministic given ``seed``. Run: ``python -m wikigraphrag.data.freshrag``.
"""

from __future__ import annotations

import json
import os
import random
from typing import List

from ..core.claim import Claim

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(ROOT, "data", "freshrag")

_ADJ = [
    "Housing", "Rural", "Green", "Urban", "Digital", "Senior", "Student", "Family",
    "Coastal", "Northern", "Southern", "Regional", "National", "Community", "Youth",
    "Veteran", "Disability", "Childcare", "Heritage", "Maritime",
]
_NOUN = [
    "Support", "Energy", "Broadband", "Health", "Education", "Transport",
    "Innovation", "Employment", "Wellbeing", "Recovery",
]
_TYPE = ["Scheme", "Grant", "Programme", "Allowance", "Fund", "Rebate", "Benefit", "Initiative"]

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]

# metric -> (relation phrase, renderer(value) -> str, (lo, hi, step))
_METRICS = {
    "income threshold": (lambda v: f"\u00a3{v:,}", (8000, 40000, 1000)),
    "monthly benefit": (lambda v: f"\u00a3{v:,}", (200, 2000, 50)),
    "application fee": (lambda v: f"\u00a3{v:,}", (20, 500, 10)),
    "maximum grant": (lambda v: f"\u00a3{v:,}", (1000, 50000, 1000)),
    "annual allowance": (lambda v: f"\u00a3{v:,}", (500, 10000, 250)),
    "age limit": (lambda v: f"{v} years", (16, 70, 1)),
}
_METRIC_NAMES = list(_METRICS)


def _scheme_names(rng: random.Random, n: int) -> List[str]:
    combos = [f"{a} {nn} {t}" for a in _ADJ for nn in _NOUN for t in _TYPE if a != nn]
    rng.shuffle(combos)
    return combos[:n]


def build(n_schemes: int = 100, seed: int = 0, write: bool = True) -> List[Claim]:
    rng = random.Random(seed)
    schemes = _scheme_names(rng, n_schemes)
    claims: List[Claim] = []

    for scheme in schemes:
        metric = rng.choice(_METRIC_NAMES)
        render, (lo, hi, step) = _METRICS[metric]
        history_len = rng.choice([2, 2, 3, 3, 4])  # skew toward short histories
        start_val = rng.randrange(lo, hi - step * history_len, step)
        year = rng.randrange(2015, 2019)
        val = start_val
        seq = []
        for _ in range(history_len):
            seq.append((year, rng.choice(_MONTHS), val))
            val += rng.randrange(step, step * 4, step)  # strictly increasing -> real change
            year += rng.randrange(1, 3)

        # optionally re-announce the latest value later (a restatement, not a change)
        add_restatement = rng.random() < 0.25
        for i, (yr, month, value) in enumerate(seq):
            claims.append(_circular(scheme, metric, render, yr, month, value, i))
        if add_restatement:
            yr, month, value = seq[-1][0] + 1, rng.choice(_MONTHS), seq[-1][2]
            claims.append(_circular(scheme, metric, render, yr, month, value, len(seq), restate=True))

    claims.sort(key=lambda c: (c.subject or "", c.timestamp or 0.0))
    if write:
        from .io import save_claims

        save_claims(claims, os.path.join(OUT_DIR, "claims.jsonl"))
        _write_meta(claims, os.path.join(OUT_DIR, "meta.json"))
    return claims


def _circular(scheme, metric, render, year, month, value, idx, restate=False) -> Claim:
    value_str = render(value)
    text = f"In {month} {year}, the {metric} for the {scheme} is {value_str}."
    ts = year + (_MONTHS.index(month) + 0.5) / 12.0
    tag = "r" if restate else str(idx)
    return Claim(
        cid=f"fr:{scheme.replace(' ', '_')}:{metric.replace(' ', '_')}:{year}:{tag}",
        text=text,
        timestamp=ts,
        doc_id=f"circular:{scheme}:{year}",
        subject=scheme,
        relation=metric,
        value=value_str,
        meta={"scheme": scheme, "metric": metric, "restatement": restate},
    )


def _write_meta(claims: List[Claim], path: str) -> None:
    from ..eval.detection import gold_superseded

    sup = gold_superseded(claims)
    keys = {c.gold_key for c in claims}
    meta = {
        "name": "freshrag",
        "claims": len(claims),
        "superseded": len(sup),
        "active": len(claims) - len(sup),
        "cases": len(keys),
        "restatements": sum(1 for c in claims if c.meta.get("restatement")),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


if __name__ == "__main__":
    cs = build(write=True)
    from ..eval.detection import gold_superseded

    print(json.dumps({
        "claims": len(cs), "superseded": len(gold_superseded(cs)),
        "cases": len({c.gold_key for c in cs}),
        "restatements": sum(1 for c in cs if c.meta.get("restatement")),
    }, indent=2))

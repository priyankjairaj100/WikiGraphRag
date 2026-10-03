"""W2: realistic, non-value-agnostic current-answer QA on genuine Wikipedia prose.

The flagship $+0.17$ EM gain is measured on Wikidata rendered as \emph{plain present-tense}
statements with recency only in document metadata -- exactly Lemma 1's value-agnostic-relevance
regime, which maximizes the flat-retrieval failure. This runner asks the same current-value
question on the RealProse genuine Wikipedia leads, whose text carries the recency cues real
corpora actually have (tense; ``succeeding Steve Ballmer in 2014''; ``served ... from 2000 to
2014''; ``since 2017''), so a reader can often disambiguate by reading alone. The gap between
flat and lifecycle here bounds how much of the headline gain survives on genuine prose.

End to end: hybrid retrieval over all RealProse claims, then flat / date-prompt / date-rerank /
lifecycle / gold filtering feed the same reader. Uses the local Qwen reader (cached); the
extractive reader is a deterministic cross-check.

Usage: python -m experiments.run_qa_realprose [--reader qwen|extractive] [--k 5]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import gold_superseded, _norm_value  # noqa: E402
from wikigraphrag.eval.qa import clean_match, exact_match, normalize_answer  # noqa: E402
from wikigraphrag.eval.stats import paired_bootstrap  # noqa: E402
from wikigraphrag.readers.base import build_prompt, build_prompt_dated, build_prompt_neutral  # noqa: E402
from wikigraphrag.retrieve.hybrid import HybridRetriever  # noqa: E402
from wikigraphrag.retrieve.lifecycle import date_rerank, lifecycle_rerank  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_Q = {
    "chief executive officer": "Who is the current chief executive officer of {s}?",
    "prime minister": "Who is the current prime minister of {s}?",
    "chancellor": "Who is the current chancellor of {s}?",
    "president": "Who is the current president of {s}?",
    "manager": "Who is the current manager of {s}?",
    "secretary-general": "Who is the current secretary-general of {s}?",
    "chair": "Who is the current chair of {s}?",
    "pope": "Who is the current pope?",
}

_CONDS = ("flat", "date_prompt", "date_rerank", "lifecycle", "gold")


def _name_hit(ans: str, gold: str) -> bool:
    """Surname-robust match: a hit if the gold appears verbatim, or the answer shares a
    distinctive name token (length >= 4) with the gold. This scores a reader that echoes the
    full formal name from the prose (``Satya Narayana Nadella``) against a short display gold
    (``Satya Nadella``), which plain substring EM would miss."""
    if exact_match(ans, gold):
        return True
    gt = {t for t in normalize_answer(gold).split() if len(t) >= 4}
    at = set(normalize_answer(ans).split())
    return bool(gt & at)


def _name_clean(ans: str, gold: str, stale: List[str]) -> bool:
    """Strict: the answer names the current holder and none of the superseded ones."""
    return _name_hit(ans, gold) and not any(_name_hit(ans, s) for s in stale)


def _questions(claims: List[Claim]) -> List[Tuple[str, str, List[str], Tuple[str, str]]]:
    by_key: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)
    out = []
    for (subject, relation), group in by_key.items():
        if relation not in _Q:
            continue
        values = {_norm_value(c.value) for c in group}
        if len(values) < 2:
            continue  # need a real change for a current-vs-stale question
        active = max(group, key=lambda c: c.timestamp)
        stale = [c.value for c in group if _norm_value(c.value) != _norm_value(active.value)]
        tmpl = _Q[relation]
        q = tmpl.format(s=subject) if "{s}" in tmpl else tmpl
        out.append((q, active.value, stale, (subject, relation)))
    return out


def _make_reader(name: str):
    if name == "extractive":
        from wikigraphrag.readers.base import ExtractiveReader
        return ExtractiveReader()
    if name == "qwen":
        from wikigraphrag.readers.qwen import QwenReader
        return QwenReader(max_new_tokens=24)
    raise SystemExit(f"unknown reader {name!r}")


def _answer(reader, name: str, question: str, ctx: List[Claim], cond: str) -> str:
    if name == "extractive":
        return reader.answer(question, ctx)
    if cond == "date_prompt":
        prompt = build_prompt_dated(question, ctx)
    elif cond == "flat":
        prompt = build_prompt(question, ctx)  # neutral trust-current instruction, dates shown
    else:
        prompt = build_prompt_neutral(question, ctx)
    return reader.generate(prompt, max_new_tokens=24)


def run(reader_name: str, k: int) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", "realprose", "claims.jsonl"))
    retr = HybridRetriever(claims)
    by_cid = {c.cid: c for c in claims}
    ts = {c.cid: (c.timestamp or 0.0) for c in claims}
    key_of = {c.cid: c.gold_key for c in claims}
    gold_S = gold_superseded(claims)
    det_S = {e.older for e in SupersessionDetector().detect(claims)}
    reader = _make_reader(reader_name)
    questions = _questions(claims)

    em: Dict[str, List[float]] = {c: [] for c in _CONDS}
    clean: Dict[str, List[float]] = {c: [] for c in _CONDS}
    ser = {"flat": 0, "lifecycle": 0}
    aep_num = {"flat": 0.0, "lifecycle": 0.0}
    aep_den = {"flat": 0, "lifecycle": 0}

    for question, gold_value, stale, key in questions:
        ranked = retr.ranked_cids(question)
        for cond in _CONDS:
            if cond == "date_rerank":
                order = date_rerank(ranked, ts)
            elif cond == "lifecycle":
                order = lifecycle_rerank(ranked, det_S)
            elif cond == "gold":
                order = lifecycle_rerank(ranked, gold_S)
            else:
                order = ranked
            ctx = [by_cid[c] for c in order[:k]]
            ans = _answer(reader, reader_name, question, ctx, cond)
            em[cond].append(float(_name_hit(ans, gold_value)))
            clean[cond].append(float(_name_clean(ans, gold_value, stale)))
            if cond in ("flat", "lifecycle"):
                keyed = [c for c in order[:k] if key_of[c] == key]
                if any(c in gold_S for c in keyed):
                    ser[cond] += 1
                if keyed:
                    aep_num[cond] += sum(1 for c in keyed if c not in gold_S) / len(keyed)
                    aep_den[cond] += 1

    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0
    n = len(questions)
    card = {
        "dataset": "realprose", "reader": reader_name, "k": k, "n_questions": n,
        "note": "current-value QA on genuine Wikipedia leads with natural recency cues; "
                "realistic (non-value-agnostic) end-to-end regime",
        "current_answer_em": {c: mean(em[c]) for c in _CONDS},
        "clean_em": {c: mean(clean[c]) for c in _CONDS},
        "ser": {c: round(ser[c] / n, 4) for c in ("flat", "lifecycle")},
        "aep": {c: round(aep_num[c] / aep_den[c], 4) if aep_den[c] else 0.0
                for c in ("flat", "lifecycle")},
        "lifecycle_vs_flat": paired_bootstrap(em["lifecycle"], em["flat"]),
        "lifecycle_vs_date_rerank": paired_bootstrap(em["lifecycle"], em["date_rerank"]),
    }
    out = os.path.join(ROOT, "results", f"qa_realprose_{reader_name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, ensure_ascii=False)
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader", default="qwen")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    print(json.dumps(run(args.reader, args.k), indent=2, ensure_ascii=False))

"""End-to-end current-answer QA: retrieval condition x reader -> exact match (Fig 2 / Table 5).

Compares flat / lifecycle / gold retrieval feeding the same reader, on "what is the current
value?" questions whose gold answer is the latest value per key. Writes
``results/qa_<name>_<reader>.json``.

Usage: ``python -m experiments.run_qa wikidata --reader extractive --k 5``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.data.io import load_claims  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.eval.detection import gold_superseded  # noqa: E402
from wikigraphrag.eval.qa import clean_match, exact_match  # noqa: E402
from wikigraphrag.eval.stats import paired_bootstrap  # noqa: E402
from wikigraphrag.readers.base import ExtractiveReader, build_prompt, build_prompt_dated  # noqa: E402
from wikigraphrag.retrieve.hybrid import HybridRetriever  # noqa: E402
from wikigraphrag.retrieve.lifecycle import lifecycle_rerank, date_rerank  # noqa: E402
from wikigraphrag.retrieve.temporal import ragtime_scores, ranked_cids, tempralm_scores  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_QUESTION = {
    "head of government": "Who is the current head of government of {s}?",
    "chief executive officer": "Who is the current CEO of {s}?",
}

_RAGTIME = {
    "ragtime_default": {"alpha": 0.7, "half_life_days": 14.0},
    "ragtime_tuned": {"alpha": 0.7, "half_life_days": 1825.0},
}

_CONDITIONS = (
    "flat", "date_prompt", "date_rerank", "tempralm",
    "ragtime_default", "ragtime_tuned", "lifecycle", "gold",
)


def _questions(claims: List[Claim]) -> List[Tuple[str, str, List[str]]]:
    """Return ``[(question, gold_value, active_claim)]`` for each multi-value chronology key."""
    by_key: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
    for c in claims:
        if c.gold_key is not None and c.timestamp is not None:
            by_key[c.gold_key].append(c)
    qs: List[Tuple[str, str, List[str]]] = []
    for (subject, relation), group in by_key.items():
        if len(group) < 2:
            continue
        active = max(group, key=lambda c: c.timestamp)
        stale = list({c.value for c in group if c.value != active.value})
        template = group[0].meta.get("query") if group[0].meta else None
        if template and "_X_" in template:
            statement = template.replace("_X_", "____").strip()
            question = ("As of the most recent date, what single value fills the blank? "
                        f"{statement}")
        elif relation in _QUESTION:
            question = _QUESTION[relation].format(s=group[0].subject)
        else:
            question = f"What is the current {relation} of {group[0].subject}?"
        qs.append((question, active.value, stale))
    return qs


def _make_reader(name: str):
    if name == "extractive":
        return ExtractiveReader()
    if name == "qwen":
        from wikigraphrag.readers.qwen import QwenReader

        return QwenReader()
    if name.startswith("gpt-"):
        from wikigraphrag.readers.openai_reader import OpenAIReader

        return OpenAIReader(model_name=name, max_new_tokens=24)
    raise SystemExit(f"unknown reader {name!r}")


def run(
    name: str,
    reader_name: str,
    k: int,
    conditions: Tuple[str, ...] = _CONDITIONS,
) -> dict:
    unknown = set(conditions) - set(_CONDITIONS)
    if unknown:
        raise ValueError(f"unknown conditions: {sorted(unknown)}")
    print(f"[1/4] loading {name} claims and index", flush=True)
    claims = load_claims(os.path.join(ROOT, "data", name, "claims.jsonl"))
    retr = HybridRetriever(claims)
    by_cid = {c.cid: c for c in claims}
    ts = {c.cid: (c.timestamp or 0.0) for c in claims}
    times = np.asarray([c.timestamp or 0.0 for c in claims], dtype=np.float64)
    reference_time = float(times.max())
    gold_S = gold_superseded(claims)
    det_S = {e.older for e in SupersessionDetector().detect(claims)}
    reader = _make_reader(reader_name)

    questions = _questions(claims)
    print(f"[2/4] preparing {len(questions)} questions", flush=True)
    retr.embedder.encode([question for question, _gold, _stale in questions])
    print("[3/4] scoring all conditions", flush=True)
    hits: Dict[str, List[float]] = {cond: [] for cond in conditions}
    clean: Dict[str, List[float]] = {cond: [] for cond in conditions}
    records = []
    for question, gold_value, stale in questions:
        base = retr.ranked_cids(question)
        semantic = retr.dense_scores(question)
        orders = {
            "flat": base,
            "date_prompt": base,
            "date_rerank": date_rerank(base, ts),
            "tempralm": ranked_cids(
                retr.cids, tempralm_scores(semantic, times, reference_time)
            ),
            "lifecycle": lifecycle_rerank(base, det_S),
            "gold": lifecycle_rerank(base, gold_S),
        }
        for cond, setting in _RAGTIME.items():
            orders[cond] = ranked_cids(
                retr.cids,
                ragtime_scores(semantic, times, reference_time, **setting),
            )
        for cond in conditions:
            ctx = [by_cid[c] for c in orders[cond][:k]]
            prompt = build_prompt_dated(question, ctx) if cond == "date_prompt" \
                else build_prompt(question, ctx)
            records.append((cond, question, ctx, prompt, gold_value, stale))

    if hasattr(reader, "generate_many"):
        answers = reader.generate_many(
            [record[3] for record in records], max_new_tokens=24
        )
    elif hasattr(reader, "generate"):
        answers = [reader.generate(record[3], max_new_tokens=24) for record in records]
    else:
        answers = [reader.answer(record[1], record[2]) for record in records]

    for record, ans in zip(records, answers):
        cond, _question, _ctx, _prompt, gold_value, stale = record
        hits[cond].append(float(exact_match(ans, gold_value)))
        clean[cond].append(float(clean_match(ans, gold_value, stale)))
    n = max(1, len(questions))
    scores = {cond: round(sum(values) / n, 4) for cond, values in hits.items()}
    scores_strict = {
        cond: round(sum(values) / n, 4) for cond, values in clean.items()
    }

    card = {
        "dataset": name, "reader": reader_name, "k": k, "n_questions": len(questions),
        "current_answer_em": scores,
        "current_answer_em_strict": scores_strict,
        "paired_tests": {
            f"lifecycle_vs_{cond}": paired_bootstrap(hits["lifecycle"], hits[cond])
            for cond in conditions if cond not in ("lifecycle", "gold")
        } if "lifecycle" in conditions else {},
    }
    out_dir = os.path.join(ROOT, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"qa_{name}_{reader_name}.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2)
    print("[4/4] result card written", flush=True)
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", nargs="?", default="wikidata")
    ap.add_argument("--reader", default="extractive")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument(
        "--conditions",
        default=",".join(_CONDITIONS),
        help="comma-separated subset of retrieval conditions",
    )
    args = ap.parse_args()
    selected = tuple(condition.strip() for condition in args.conditions.split(",") if condition.strip())
    print(json.dumps(run(args.dataset, args.reader, args.k, selected), indent=2))

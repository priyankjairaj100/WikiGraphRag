"""Evaluate published temporal-RAG baselines before considering paper inclusion.

Baselines:
* TempRALM (Gade and Jetcheva, 2024): semantic relevance plus a
  distribution-matched inverse query/document time-distance score, with future
  evidence masked.
* RAG-Time (Grofsky, 2025): convex fusion with a half-life recency prior. We
  report the paper's default and its stronger published real-data setting.

Both methods score the full corpus from raw dense cosine similarities. This is
stronger than reordering a narrow semantic top-k because a
relevant recent item cannot be discarded before the temporal score acts.

Usage:
  python -m experiments.run_temporal_rag_baselines --reader extractive
  python -m experiments.run_temporal_rag_baselines --reader qwen
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

import numpy as np

from wikigraphrag.core.claim import Claim
from wikigraphrag.data.io import load_claims
from wikigraphrag.detect import SupersessionDetector
from wikigraphrag.eval.detection import _norm_value, gold_superseded
from wikigraphrag.eval.qa import clean_match, exact_match
from wikigraphrag.eval.stats import paired_bootstrap
from wikigraphrag.lifecycle.intervals import as_of, derive_timelines
from wikigraphrag.readers.base import ExtractiveReader, build_prompt
from wikigraphrag.retrieve.hybrid import HybridRetriever
from wikigraphrag.retrieve.lifecycle import lifecycle_rerank
from wikigraphrag.retrieve.temporal import ragtime_scores, ranked_cids, tempralm_scores

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_QUESTION = {
    "head of government": "Who is the current head of government of {s}?",
    "chief executive officer": "Who is the current CEO of {s}?",
}

_RAGTIME_SETTINGS = {
    "ragtime_default": {"alpha": 0.7, "half_life_days": 14.0},
    "ragtime_real_best": {"alpha": 0.3, "half_life_days": 14.0},
    "ragtime_tuned": {"alpha": 0.7, "half_life_days": 1825.0},
}


def _make_reader(name: str):
    if name == "extractive":
        return ExtractiveReader()
    if name == "qwen":
        from wikigraphrag.readers.qwen import QwenReader
        return QwenReader(max_new_tokens=24)
    raise SystemExit(f"unknown reader {name!r}")


def _answer(reader, reader_name: str, question: str, contexts: Sequence[Claim]) -> str:
    if reader_name == "extractive":
        return reader.answer(question, contexts)
    return reader.generate(build_prompt(question, contexts), max_new_tokens=24)


def _current_questions(claims: Sequence[Claim]) -> List[Tuple[str, str, List[str]]]:
    by_key: Dict[object, List[Claim]] = defaultdict(list)
    for claim in claims:
        if claim.gold_key is not None and claim.timestamp is not None:
            by_key[claim.gold_key].append(claim)

    questions = []
    for group in by_key.values():
        relation = group[0].relation
        if len(group) < 2 or relation not in _QUESTION:
            continue
        active = max(group, key=lambda c: c.timestamp)
        stale = sorted({c.value for c in group if c.value != active.value})
        question = _QUESTION[relation].format(s=group[0].subject)
        questions.append((question, active.value, stale))
    return questions


def _temporal_orders(
    retriever: HybridRetriever,
    question: str,
    timestamps: np.ndarray,
    query_time: float,
) -> Dict[str, List[str]]:
    semantic = retriever.dense_scores(question)
    orders = {
        "tempralm": ranked_cids(
            retriever.cids, tempralm_scores(semantic, timestamps, query_time)
        )
    }
    for name, setting in _RAGTIME_SETTINGS.items():
        scores = ragtime_scores(semantic, timestamps, query_time, **setting)
        orders[name] = ranked_cids(retriever.cids, scores)
    return orders


def _current_answer_block(reader_name: str, k: int = 5) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", "wikidata", "claims.jsonl"))
    retriever = HybridRetriever(claims)
    by_cid = {c.cid: c for c in claims}
    times = np.asarray([c.timestamp or 0.0 for c in claims], dtype=np.float64)
    reference_time = float(times.max())
    det_s = {e.older for e in SupersessionDetector().detect(claims)}
    gold_s = gold_superseded(claims)
    reader = _make_reader(reader_name)
    questions = _current_questions(claims)
    conditions = (
        "flat", "tempralm", "ragtime_default", "ragtime_real_best",
        "ragtime_tuned", "lifecycle", "gold"
    )
    hits = {condition: [] for condition in conditions}
    strict = {condition: [] for condition in conditions}

    for question, gold_value, stale_values in questions:
        base = retriever.ranked_cids(question)
        orders = _temporal_orders(retriever, question, times, reference_time)
        orders.update({
            "flat": base,
            "lifecycle": lifecycle_rerank(base, det_s),
            "gold": lifecycle_rerank(base, gold_s),
        })
        for condition in conditions:
            contexts = [by_cid[cid] for cid in orders[condition][:k]]
            answer = _answer(reader, reader_name, question, contexts)
            hits[condition].append(float(exact_match(answer, gold_value)))
            strict[condition].append(float(clean_match(answer, gold_value, stale_values)))

    n = max(1, len(questions))
    return {
        "dataset": "wikidata",
        "reader": reader_name,
        "k": k,
        "n_questions": len(questions),
        "current_answer_em": {
            key: round(sum(value) / n, 4) for key, value in hits.items()
        },
        "current_answer_em_strict": {
            key: round(sum(value) / n, 4) for key, value in strict.items()
        },
        "paired_tests": {
            f"lifecycle_vs_{method}": paired_bootstrap(hits["lifecycle"], hits[method])
            for method in (
                "tempralm", "ragtime_default", "ragtime_real_best", "ragtime_tuned"
            )
        },
    }


def _gold_timelines(claims: Sequence[Claim]) -> Dict[object, List[Claim]]:
    by_key: Dict[object, List[Claim]] = defaultdict(list)
    for claim in claims:
        if claim.gold_key is not None and claim.timestamp is not None:
            by_key[claim.gold_key].append(claim)
    return {key: sorted(group, key=lambda c: c.timestamp) for key, group in by_key.items()}


def _asof_question(group: Sequence[Claim], query_time: float) -> str:
    template = group[0].meta.get("query") if group[0].meta else None
    if template and "_X_" in template:
        statement = template.replace("_X_", "____").strip()
        return (f"As of {query_time:.2f}, what single value fills the blank? "
                f"{statement}")
    return f"What was the {group[0].relation} of {group[0].subject} as of {query_time:.2f}?"


def _asof_block(dataset: str) -> dict:
    claims = load_claims(os.path.join(ROOT, "data", dataset, "claims.jsonl"))
    retriever = HybridRetriever(claims)
    by_cid = {c.cid: c for c in claims}
    times = np.asarray([c.timestamp or 0.0 for c in claims], dtype=np.float64)
    gold = _gold_timelines(claims)
    detector_timelines = derive_timelines(claims, SupersessionDetector().detect(claims))
    detector_comp = {
        interval.cid: component
        for component, timeline in detector_timelines.items()
        for interval in timeline
    }

    historical: List[Tuple[object, float, str]] = []
    current: List[Tuple[object, float, str]] = []
    for key, group in gold.items():
        points: List[Claim] = []
        for claim in group:
            if not points or _norm_value(points[-1].value) != _norm_value(claim.value):
                points.append(claim)
        if len(points) < 2:
            continue
        for index, claim in enumerate(points):
            if index + 1 < len(points):
                next_time = points[index + 1].timestamp
                historical.append((key, (claim.timestamp + next_time) / 2.0,
                                   _norm_value(claim.value)))
            else:
                current.append((key, claim.timestamp + 1.0, _norm_value(claim.value)))

    methods = (
        "tempralm", "ragtime_default", "ragtime_real_best",
        "ragtime_tuned", "lifecycle_asof",
    )

    def score(probes: Sequence[Tuple[object, float, str]]) -> dict:
        hits = {method: [] for method in methods}
        for key, query_time, gold_value in probes:
            group = gold[key]
            question = _asof_question(group, query_time)
            orders = _temporal_orders(retriever, question, times, query_time)
            for method in methods[:-1]:
                top = by_cid[orders[method][0]] if orders[method] else None
                prediction = _norm_value(top.value) if top and top.value else ""
                hits[method].append(float(prediction == gold_value))

            first_cid = group[0].cid
            timeline = detector_timelines.get(detector_comp.get(first_cid, ""), [])
            interval = as_of(timeline, query_time)
            prediction = _norm_value(interval.value) if interval and interval.value else ""
            hits["lifecycle_asof"].append(float(prediction == gold_value))

        n = max(1, len(probes))
        return {
            "accuracy": {
                method: round(sum(value) / n, 4) for method, value in hits.items()
            },
            "paired_tests": {
                f"lifecycle_asof_vs_{method}": paired_bootstrap(
                    hits["lifecycle_asof"], hits[method]
                )
                for method in (
                    "tempralm", "ragtime_default", "ragtime_real_best", "ragtime_tuned"
                )
            },
        }

    return {
        "dataset": dataset,
        "n_historical_probes": len(historical),
        "n_current_probes": len(current),
        "historical_accuracy": score(historical),
        "current_accuracy": score(current),
    }


def run(reader_name: str, k: int = 5) -> dict:
    card = {
        "methods": {
            "tempralm": "Gade and Jetcheva (2024), full-pool dense-score implementation",
            "ragtime_default": "Grofsky (2025), alpha=0.7, half-life=14 days",
            "ragtime_real_best": "Grofsky (2025), alpha=0.3, half-life=14 days",
            "ragtime_tuned": "Held-out selected alpha=0.7, half-life=1825 days",
        },
        "current_answer": _current_answer_block(reader_name, k),
        "as_of": {
            dataset: _asof_block(dataset)
            for dataset in ("wikidata", "templama", "realworld")
        },
    }
    output = os.path.join(ROOT, "results", f"temporal_rag_baselines_{reader_name}.json")
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(card, handle, indent=2)
    return card


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reader", choices=("extractive", "qwen"), default="extractive")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run(args.reader, args.k), indent=2))
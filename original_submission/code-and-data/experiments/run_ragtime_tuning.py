"""Held-out tuning for the RAG-Time half-life baseline.

One setting is selected on deterministic Wikidata development keys, maximizing
the unweighted mean of current-answer and historical as-of accuracy. The setting
is then frozen for disjoint Wikidata test keys and the complete TempLAMA and
Real-world datasets. This avoids per-dataset or per-task test-set tuning.

Usage: ``python -m experiments.run_ragtime_tuning``
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

import numpy as np

from wikigraphrag.core.claim import Claim
from wikigraphrag.data.io import load_claims
from wikigraphrag.detect import SupersessionDetector
from wikigraphrag.eval.detection import _norm_value
from wikigraphrag.eval.stats import paired_bootstrap
from wikigraphrag.lifecycle.intervals import as_of, derive_timelines
from wikigraphrag.retrieve.hybrid import HybridRetriever
from wikigraphrag.retrieve.temporal import ragtime_scores, ranked_cids

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALPHAS = (0.3, 0.5, 0.7, 0.9)
HALF_LIVES_DAYS = (14.0, 90.0, 365.0, 730.0, 1825.0)
DEV_MODULUS = 3  # one third of Wikidata chronology keys

_QUESTION = {
    "head of government": "Who is the current head of government of {s}?",
    "chief executive officer": "Who is the current CEO of {s}?",
}


def _split(key: object) -> str:
    digest = hashlib.sha256(repr(key).encode("utf-8")).digest()
    return "dev" if int.from_bytes(digest[:8], "big") % DEV_MODULUS == 0 else "test"


def _gold_timelines(claims: Sequence[Claim]) -> Dict[object, List[Claim]]:
    groups: Dict[object, List[Claim]] = defaultdict(list)
    for claim in claims:
        if claim.gold_key is not None and claim.timestamp is not None:
            groups[claim.gold_key].append(claim)
    return {key: sorted(group, key=lambda c: c.timestamp) for key, group in groups.items()}


def _points(group: Sequence[Claim]) -> List[Claim]:
    out: List[Claim] = []
    for claim in group:
        if not out or _norm_value(out[-1].value) != _norm_value(claim.value):
            out.append(claim)
    return out


def _question(group: Sequence[Claim], query_time: float | None = None) -> str:
    template = group[0].meta.get("query") if group[0].meta else None
    if template and "_X_" in template:
        statement = template.replace("_X_", "____").strip()
        if query_time is None:
            return f"As of the most recent date, what fills the blank? {statement}"
        return f"As of {query_time:.2f}, what single value fills the blank? {statement}"
    if query_time is None:
        relation = group[0].relation
        if relation in _QUESTION:
            return _QUESTION[relation].format(s=group[0].subject)
        return f"What is the current {relation} of {group[0].subject}?"
    return f"What was the {group[0].relation} of {group[0].subject} as of {query_time:.2f}?"


def _probes(groups: Dict[object, List[Claim]]) -> Tuple[list, list]:
    current = []
    historical = []
    for key, group in groups.items():
        points = _points(group)
        if len(points) < 2:
            continue
        current.append((key, points[-1].timestamp + 1.0, _norm_value(points[-1].value)))
        for index, claim in enumerate(points[:-1]):
            next_time = points[index + 1].timestamp
            historical.append((key, (claim.timestamp + next_time) / 2.0,
                               _norm_value(claim.value)))
    return current, historical


class Evaluation:
    def __init__(self, dataset: str) -> None:
        self.dataset = dataset
        self.claims = load_claims(os.path.join(ROOT, "data", dataset, "claims.jsonl"))
        self.retriever = HybridRetriever(self.claims)
        self.by_cid = {claim.cid: claim for claim in self.claims}
        self.times = np.asarray([claim.timestamp or 0.0 for claim in self.claims])
        self.reference_time = float(self.times.max())
        self.groups = _gold_timelines(self.claims)
        self.timelines = derive_timelines(self.claims, SupersessionDetector().detect(self.claims))
        self.components = {
            interval.cid: component
            for component, timeline in self.timelines.items()
            for interval in timeline
        }
        self._semantic: Dict[str, np.ndarray] = {}

    def prepare(self, keys: Sequence[object], include_current: bool = True) -> None:
        """Batch-warm all query embeddings used by these keys."""
        subset = {key: self.groups[key] for key in keys}
        current, historical = _probes(subset)
        questions = set()
        if include_current:
            questions.update(
                _question(self.groups[key], None) for key, _time, _gold in current
            )
        questions.update(
            _question(self.groups[key], query_time)
            for key, query_time, _gold in historical
        )
        self.retriever.embedder.encode(sorted(questions))

    def _scores(self, question: str) -> np.ndarray:
        if question not in self._semantic:
            self._semantic[question] = self.retriever.dense_scores(question)
        return self._semantic[question]

    def score(
        self, keys: Sequence[object], alpha: float, half_life: float,
        include_current: bool = True,
    ) -> dict:
        subset = {key: self.groups[key] for key in keys}
        current, historical = _probes(subset)
        if not include_current:
            current = []

        def evaluate(probes: Sequence[tuple]) -> Tuple[List[float], List[float]]:
            ragtime_hits: List[float] = []
            lifecycle_hits: List[float] = []
            for key, query_time, gold_value in probes:
                group = self.groups[key]
                question = _question(group, None if probes is current else query_time)
                effective_time = self.reference_time if probes is current else query_time
                scores = ragtime_scores(
                    self._scores(question), self.times, effective_time,
                    alpha=alpha, half_life_days=half_life,
                )
                order = ranked_cids(self.retriever.cids, scores)
                top = self.by_cid[order[0]] if order else None
                prediction = _norm_value(top.value) if top and top.value else ""
                ragtime_hits.append(float(prediction == gold_value))

                timeline = self.timelines.get(self.components.get(group[0].cid, ""), [])
                interval = as_of(timeline, effective_time)
                prediction = _norm_value(interval.value) if interval and interval.value else ""
                lifecycle_hits.append(float(prediction == gold_value))
            return ragtime_hits, lifecycle_hits

        current_rag, current_lifecycle = evaluate(current)
        historical_rag, historical_lifecycle = evaluate(historical)

        def mean(values: Sequence[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        return {
            "n_keys": len(keys),
            "n_current": len(current),
            "n_historical": len(historical),
            "current_accuracy": round(mean(current_rag), 4),
            "historical_accuracy": round(mean(historical_rag), 4),
            "objective": round((mean(current_rag) + mean(historical_rag)) / 2.0, 4),
            "lifecycle_current_accuracy": round(mean(current_lifecycle), 4),
            "lifecycle_historical_accuracy": round(mean(historical_lifecycle), 4),
            "current_paired": (
                paired_bootstrap(current_lifecycle, current_rag)
                if current_rag else None
            ),
            "historical_paired": paired_bootstrap(historical_lifecycle, historical_rag),
        }


def run() -> dict:
    print("[1/6] loading Wikidata index", flush=True)
    wikidata = Evaluation("wikidata")
    changing_keys = [key for key, group in wikidata.groups.items() if len(_points(group)) >= 2]
    dev_keys = sorted((key for key in changing_keys if _split(key) == "dev"), key=repr)
    test_keys = sorted((key for key in changing_keys if _split(key) == "test"), key=repr)
    print("[2/6] preparing Wikidata queries", flush=True)
    wikidata.prepare(dev_keys + test_keys)

    print("[3/6] sweeping development settings", flush=True)
    sweep = []
    for alpha in ALPHAS:
        for half_life in HALF_LIVES_DAYS:
            metrics = wikidata.score(dev_keys, alpha, half_life)
            sweep.append({"alpha": alpha, "half_life_days": half_life, **metrics})

    # Maximize the predeclared joint objective; prefer higher historical accuracy,
    # then the more semantic-heavy setting on exact ties.
    best = max(sweep, key=lambda row: (
        row["objective"], row["historical_accuracy"], row["current_accuracy"], row["alpha"]
    ))
    alpha = best["alpha"]
    half_life = best["half_life_days"]

    print(f"[4/6] selected alpha={alpha}, half_life={half_life:g}", flush=True)
    wikidata_test = wikidata.score(test_keys, alpha, half_life)
    del wikidata
    gc.collect()
    external = {}
    for dataset in ("templama", "realworld"):
        print(f"[5/6] evaluating {dataset}", flush=True)
        evaluation = Evaluation(dataset)
        keys = sorted(
            (key for key, group in evaluation.groups.items() if len(_points(group)) >= 2),
            key=repr,
        )
        evaluation.prepare(keys, include_current=False)
        external[dataset] = evaluation.score(
            keys, alpha, half_life, include_current=False
        )
        del evaluation
        gc.collect()

    print("[6/6] writing result card", flush=True)
    card = {
        "selection_protocol": {
            "development_data": "deterministic one-third of changing Wikidata keys",
            "objective": "mean(current-answer accuracy, historical as-of accuracy)",
            "alphas": list(ALPHAS),
            "half_lives_days": list(HALF_LIVES_DAYS),
            "single_setting_frozen_across_all_tests": True,
        },
        "n_wikidata_dev_keys": len(dev_keys),
        "n_wikidata_test_keys": len(test_keys),
        "development_sweep": sweep,
        "selected": {"alpha": alpha, "half_life_days": half_life},
        "wikidata_test": wikidata_test,
        "external_test": external,
    }
    output = os.path.join(ROOT, "results", "ragtime_heldout_tuning.json")
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(card, handle, indent=2)
    return card


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
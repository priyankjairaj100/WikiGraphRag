"""Gold-only evaluation records and history-level uncertainty summaries.

This module is never imported by the memory or extraction implementation. The
scorer checks complete value sets and annotated sufficient source sets; it does
not infer entailment from a citation, nor certify the source's truthfulness.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence

from .models import Prediction, Question, Source


def normalize_value(value: str) -> str:
    """Normalize case, Unicode and whitespace, without substring/alias matching."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _value_set(values: Iterable[str]) -> frozenset[str]:
    return frozenset(normalize_value(value) for value in values)


@dataclass(frozen=True)
class GoldAnswer:
    """Evaluation labels, deliberately separate from inference-only records.

    Each accepted_value_sets item is a complete acceptable answer (e.g. two
    co-holders), not a bag of individually sufficient names. Each sufficient
    evidence set is an alternative complete justification. Indeterminate gold
    still needs positive evidence for uncertainty, such as conflicting sources.
    An empty corpus alone is not annotated evidence for a factual state.
    """

    question_id: str
    history_id: str
    status: str
    accepted_value_sets: tuple[tuple[str, ...], ...] = ()
    sufficient_evidence_sets: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self):
        if not self.question_id or not self.history_id:
            raise ValueError("Gold requires question_id and independent history_id")
        if self.status not in {"answered", "indeterminate"}:
            raise ValueError("Gold status must be answered or indeterminate")
        if self.status == "answered" and not self.accepted_value_sets:
            raise ValueError("Answered gold requires complete accepted value sets")
        if self.status == "indeterminate" and self.accepted_value_sets:
            raise ValueError("Indeterminate gold cannot assert an accepted value set")
        if any(not values or "" in _value_set(values) for values in self.accepted_value_sets):
            raise ValueError("Accepted value sets must contain nonempty values")
        if not self.sufficient_evidence_sets or any(
            not support or any(not item for item in support)
            for support in self.sufficient_evidence_sets
        ):
            raise ValueError("Gold requires nonempty sufficient evidence source sets")


@dataclass(frozen=True)
class EvaluationResult:
    question_id: str
    history_id: str
    correct: bool
    answer_correct: bool
    evidence_sufficient: bool
    evidence_valid: bool
    source_cutoff_valid: bool
    abstained: bool
    unknown_source_ids: tuple[str, ...]
    future_source_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_prediction(
    question: Question,
    prediction: Prediction,
    gold: GoldAnswer,
    sources: Sequence[Source],
) -> EvaluationResult:
    """Score status, whole-value equality, sufficient support and cutoff jointly.

    Extra citations are permitted only when they reference available sources.
    Missing or future citations fail grounding even if another sufficient set is
    present. Labels referencing unavailable sources are an evaluation-data error,
    rather than a model error, and are rejected.
    """
    if question.question_id != prediction.question_id or question.question_id != gold.question_id:
        raise ValueError("Question, prediction and gold IDs must match")
    by_id = {source.source_id: source for source in sources}
    if len(by_id) != len(sources):
        raise ValueError("Duplicate source IDs in evaluation input")
    for support in gold.sufficient_evidence_sets:
        for source_id in support:
            if source_id not in by_id:
                raise ValueError(f"Gold references nonexistent source {source_id!r}")
            if by_id[source_id].available_at > question.information_cutoff:
                raise ValueError(f"Gold uses future source {source_id!r}")

    cited = frozenset(prediction.evidence_source_ids)
    unknown = tuple(sorted(cited - by_id.keys()))
    future = tuple(sorted(
        source_id for source_id in cited & by_id.keys()
        if by_id[source_id].available_at > question.information_cutoff
    ))
    source_cutoff_valid = not future
    evidence_valid = not unknown and source_cutoff_valid
    evidence_sufficient = any(set(support) <= cited for support in gold.sufficient_evidence_sets)
    if gold.status == "answered":
        answer_correct = prediction.status == "answered" and _value_set(prediction.values) in {
            _value_set(values) for values in gold.accepted_value_sets
        }
    else:
        answer_correct = prediction.status == "indeterminate" and not prediction.values
    reasons = []
    if not answer_correct:
        reasons.append("incorrect_status_or_complete_value_set")
    if not evidence_sufficient:
        reasons.append("no_complete_annotated_support_set")
    if unknown:
        reasons.append("nonexistent_evidence_source")
    if future:
        reasons.append("post_cutoff_evidence_source")
    return EvaluationResult(
        question.question_id, gold.history_id,
        answer_correct and evidence_sufficient and evidence_valid,
        answer_correct, evidence_sufficient, evidence_valid, source_cutoff_valid,
        prediction.status == "abstain", unknown, future, tuple(reasons),
    )


def _quantile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    fraction = position - low
    return sorted_values[low] * (1 - fraction) + sorted_values[high] * fraction


def _cluster_summary(
    history_ids: Sequence[str], scores: Sequence[float], n_resamples: int, seed: int
) -> dict:
    if len(history_ids) != len(scores) or not scores:
        raise ValueError("Aligned nonempty history IDs and scores are required")
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    groups: dict[str, list[float]] = defaultdict(list)
    for history, score in zip(history_ids, scores):
        if not history or not math.isfinite(score):
            raise ValueError("History IDs must be nonempty and scores finite")
        groups[history].append(float(score))
    means = [sum(groups[key]) / len(groups[key]) for key in sorted(groups)]
    rng = random.Random(seed)
    size = len(means)
    samples = sorted(
        sum(means[rng.randrange(size)] for _ in range(size)) / size
        for _ in range(n_resamples)
    )
    return {
        "mean": sum(means) / size,
        "ci95_low": _quantile(samples, 0.025),
        "ci95_high": _quantile(samples, 0.975),
        "n_histories": size,
        "n_items": len(scores),
        "n_resamples": n_resamples,
        "seed": seed,
        "aggregation": "equal_weight_history_means",
        "interval": "percentile_history_cluster_bootstrap",
    }


def grouped_bootstrap(
    history_ids: Sequence[str], scores: Sequence[float], *, n_resamples: int = 2000, seed: int = 0
) -> dict:
    """Macro-history accuracy CI; resample histories, never individual probes.

    This describes sampling uncertainty only when histories are independent and
    sampled suitably. It is not a risk certificate or evidence of generalization
    from the hand-authored diagnostic fixtures.
    """
    if any(not 0 <= score <= 1 for score in scores):
        raise ValueError("Accuracy scores must lie in [0, 1]")
    return _cluster_summary(history_ids, scores, n_resamples, seed)


def grouped_paired_bootstrap(
    history_ids: Sequence[str], scores_a: Sequence[float], scores_b: Sequence[float],
    *, n_resamples: int = 2000, seed: int = 0,
) -> dict:
    """History-level CI for paired score difference a-b; no bootstrap p-value."""
    if len(scores_a) != len(scores_b):
        raise ValueError("Paired scores must be aligned")
    if any(not 0 <= score <= 1 for score in (*scores_a, *scores_b)):
        raise ValueError("Accuracy scores must lie in [0, 1]")
    result = _cluster_summary(
        history_ids, [a - b for a, b in zip(scores_a, scores_b)], n_resamples, seed
    )
    result["comparison"] = "a_minus_b"
    return result

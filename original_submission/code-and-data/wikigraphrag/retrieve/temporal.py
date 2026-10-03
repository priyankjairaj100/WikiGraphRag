"""Published timestamp-only temporal retrieval baselines.

The implementations operate over the full corpus because both methods inject time
into candidate scoring, rather than reordering a semantic top-k after relevant
documents may already have been discarded.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def _stable_zscore(values: np.ndarray, target_mean: float, target_std: float) -> np.ndarray:
    std = float(values.std())
    if std < 1e-12:
        return np.full_like(values, target_mean)
    return (values - float(values.mean())) / std * target_std + target_mean


def tempralm_scores(
    semantic_scores: Sequence[float],
    timestamps: Sequence[float],
    query_time: float,
) -> np.ndarray:
    """TempRALM: semantic score plus normalized inverse temporal distance.

    Gade and Jetcheva (2024) define the temporal score as reciprocal query-document
    time distance, normalize it to the semantic score's mean and standard deviation,
    add both scores, and mask documents from the query's future. A one-day floor
    handles a document timestamp equal to the query timestamp without changing the
    ordering of nonzero distances.
    """
    semantic = np.asarray(semantic_scores, dtype=np.float64)
    times = np.asarray(timestamps, dtype=np.float64)
    if semantic.shape != times.shape:
        raise ValueError("semantic_scores and timestamps must have the same shape")

    delta_days = np.maximum((query_time - times) * 365.25, 1.0)
    temporal_raw = 1.0 / delta_days
    temporal = _stable_zscore(temporal_raw, float(semantic.mean()), float(semantic.std()))
    combined = semantic + temporal
    combined[times > query_time] = -np.inf
    return combined


def ragtime_scores(
    semantic_scores: Sequence[float],
    timestamps: Sequence[float],
    reference_time: float,
    alpha: float = 0.7,
    half_life_days: float = 14.0,
) -> np.ndarray:
    """RAG-Time: convex fusion of semantic relevance and half-life recency.

    Grofsky (2025) uses ``alpha * semantic + (1-alpha) * 0.5 ** (age/h)``
    with defaults ``alpha=0.7`` and ``h=14 days``. Future documents are masked so
    the same function can be evaluated on historical as-of questions.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    if half_life_days <= 0.0:
        raise ValueError("half_life_days must be positive")

    semantic = np.asarray(semantic_scores, dtype=np.float64)
    times = np.asarray(timestamps, dtype=np.float64)
    if semantic.shape != times.shape:
        raise ValueError("semantic_scores and timestamps must have the same shape")

    age_days = np.maximum((reference_time - times) * 365.25, 0.0)
    recency = np.power(0.5, age_days / half_life_days)
    combined = alpha * semantic + (1.0 - alpha) * recency
    combined[times > reference_time] = -np.inf
    return combined


def ranked_cids(cids: Sequence[str], scores: Sequence[float]) -> list[str]:
    """Return candidate identifiers in descending score order."""
    values = np.asarray(scores, dtype=np.float64)
    if len(cids) != len(values):
        raise ValueError("cids and scores must have the same length")
    return [cids[i] for i in np.argsort(-values, kind="stable")]
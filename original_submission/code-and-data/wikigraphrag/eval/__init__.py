"""Evaluation metrics: detection (P/R/F1), retrieval (SER/AEP), QA (EM)."""

from .detection import gold_superseded, gold_active_by_key, evaluate_detection
from .retrieval import evaluate_retrieval, make_current_fact_queries

__all__ = [
    "gold_superseded",
    "gold_active_by_key",
    "evaluate_detection",
    "evaluate_retrieval",
    "make_current_fact_queries",
]

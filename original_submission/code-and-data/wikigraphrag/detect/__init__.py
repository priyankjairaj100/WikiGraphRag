"""The text-driven supersession detector (Algorithm 1) and its three signals."""

from .features import ClaimFeatures, extract_features
from .signals import (
    same_subject_relation,
    changed_value,
    orient_by_recency,
    recency_confidence,
)
from .supersession import SupersessionDetector, detect_supersession

__all__ = [
    "ClaimFeatures",
    "extract_features",
    "same_subject_relation",
    "changed_value",
    "orient_by_recency",
    "recency_confidence",
    "SupersessionDetector",
    "detect_supersession",
]

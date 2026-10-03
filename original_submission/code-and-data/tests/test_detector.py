"""Golden tests for the zero-LLM supersession detector.

Runnable with no third-party deps: ``python tests/test_detector.py``. Each case probes one
behaviour the paper asserts -- a real supersession fires, and the precision guards
(different subjects, generic suffixes, restatements) do not.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wikigraphrag.core.claim import Claim  # noqa: E402
from wikigraphrag.detect import SupersessionDetector  # noqa: E402
from wikigraphrag.retrieve.lifecycle import date_rerank, lifecycle_rerank  # noqa: E402


def _c(cid, text, year, **kw):
    return Claim(cid=cid, text=text, timestamp=float(year), **kw)


def _edges(claims):
    return SupersessionDetector().detect(claims)


def test_ceo_substitution_fires():
    claims = [
        _c("a", "The CEO of Acme is Alice Smith.", 2023),
        _c("b", "The CEO of Acme is Bob Jones.", 2024),
    ]
    e = _edges(claims)
    assert len(e) == 1, e
    assert e[0].newer == "b" and e[0].older == "a"


def test_head_of_government_change_fires():
    claims = [
        _c("uk23", "The head of government of the United Kingdom is Rishi Sunak.", 2023),
        _c("uk24", "The head of government of the United Kingdom is Keir Starmer.", 2024),
    ]
    e = _edges(claims)
    assert len(e) == 1 and e[0].newer == "uk24" and e[0].older == "uk23", e


def test_different_subject_same_relation_does_not_link():
    # UK and France share the relation but not the subject: no edge either way.
    claims = [
        _c("uk", "The head of government of the United Kingdom is Rishi Sunak.", 2023),
        _c("fr", "The head of government of France is Emmanuel Macron.", 2024),
    ]
    assert _edges(claims) == [], _edges(claims)


def test_generic_suffix_does_not_cross_link():
    # Two subjects sharing only the generic suffix "Party" must not link (Section 3).
    claims = [
        _c("lib", "The leader of the Liberal Party is Ed Davey.", 2021),
        _c("rep", "The leader of the Republican Party is Ronna McDaniel.", 2024),
    ]
    assert _edges(claims) == [], _edges(claims)


def test_restatement_is_not_a_change():
    # Same value, reworded -> a restatement, not a substitution.
    claims = [
        _c("a", "The CEO of Acme is Alice Smith.", 2023),
        _c("b", "Acme's CEO is Alice Smith.", 2024),
    ]
    assert _edges(claims) == [], _edges(claims)


def test_numeric_threshold_change_fires():
    claims = [
        _c("t1", "The income threshold of Scheme A is 12,000 pounds.", 2022),
        _c("t2", "The income threshold of Scheme A is 15,000 pounds.", 2024),
    ]
    e = _edges(claims)
    assert len(e) == 1 and e[0].newer == "t2", e


def test_keeps_only_newest_superseder():
    # Three values of one key: only the newest supersedes each older one, and the newest
    # itself is never superseded -> exactly two edges, none incoming to the latest.
    claims = [
        _c("v1", "The CEO of Acme is Alice Smith.", 2021),
        _c("v2", "The CEO of Acme is Bob Jones.", 2022),
        _c("v3", "The CEO of Acme is Carol Lee.", 2023),
    ]
    e = _edges(claims)
    older = {x.older for x in e}
    assert older == {"v1", "v2"}, e
    assert all(x.newer == "v3" for x in e), e


def test_recency_confidence_cue():
    claims = [
        _c("a", "The CEO of Acme is Alice Smith.", 2023),
        _c("b", "Effective 2024, the CEO of Acme is now Bob Jones.", 2024),
    ]
    e = _edges(claims)
    # graded match-confidence: a strong match with an explicit update cue is high-confidence
    assert len(e) == 1 and e[0].confidence >= 0.8, e


def test_lifecycle_filter_removes_stale_claims():
    ranked = ["stale-a", "active-a", "stale-b", "active-b"]
    assert lifecycle_rerank(ranked, {"stale-a", "stale-b"}) == ["active-a", "active-b"]


def test_date_rerank_is_newest_first():
    ranked = ["middle", "oldest", "newest"]
    timestamps = {"oldest": 2020.0, "middle": 2022.0, "newest": 2024.0}
    assert date_rerank(ranked, timestamps) == ["newest", "middle", "oldest"]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)

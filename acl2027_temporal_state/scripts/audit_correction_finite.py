#!/usr/bin/env python3
"""Independent finite-ledger oracle; synthetic implementation audit only.

The expected active ledger is obtained by exhaustively enumerating all subsets
that exclude correction targets and satisfy essential-support implications.
This does not call the production projection to compute expected results.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import product
import json
from math import isclose
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.corrections import (
    CorrectionEvidence, CorrectionPolicy, SourceAuthority, SupportDependency,
    make_correction_state_semantics, materialize_corrected_selection,
)
from temporal_state.coupled import TemporalInfeasible
from temporal_state.correction_pipeline import _objective_parts
from temporal_state.decoder import Link, Mention, Penalty, Problem, Reading, evaluate_assignment
from temporal_state.models import Question, Source


def brute_active(selected, correction_edges, dependencies):
    """Greatest supported subset, determined by finite set enumeration."""
    forbidden = {target for _, target in correction_edges}
    selected = set(selected)
    admissible = []
    for bits in product((False, True), repeat=4):
        subset = {i for i, bit in enumerate(bits) if bit}
        if not subset <= selected or subset & forbidden:
            continue
        if all(dependent not in subset or prerequisite in subset
               for dependent, prerequisite in dependencies):
            admissible.append(subset)
    largest = [subset for subset in admissible
               if all(other <= subset for other in admissible)]
    assert len(largest) == 1, "The conjunctive support system must have one greatest subset"
    return largest[0]


def fixture(correction_parents, support_parents):
    sources, mentions, claims, links = [], [], {}, []
    authorities, certificates, support = [], [], []
    cue = "corrects the earlier record"
    dependency = "Copy relies on the earlier record"
    readings, chosen = {}, {}
    for i in range(4):
        mid, sid, cid = f"m{i}", f"s{i}", f"c{i}"
        text = f"Org {cue}. A served as CEO. {dependency}."
        available = f"2024-01-{20 + i:02d}"
        sources.append(Source(sid, text, available))
        mentions.append(Mention(mid, sid, 0, len(text), (
            Reading("r", ("Org", "CEO", ""), "A", 1),
            Reading("u", None, None, 0))))
        claims[mid, "r"] = TemporalClaim(
            cid, sid, "Org", "CEO", "A", reported_at=available,
            start=DayBounds(f"2024-01-{i + 1:02d}", f"2024-01-{i + 1:02d}"),
            end=DayBounds("2024-01-08", "2024-01-08"),
            evidence_spans=(EvidenceSpan(0, len(text), text),))
        authorities.append(SourceAuthority(sid, "org", (EvidenceSpan(0, 3, "Org"),)))
        readings[mid], chosen[mid] = "r", None
        target = correction_parents[i]
        if target is not None:
            lid = f"correct-{i}-{target}"
            span = EvidenceSpan(text.index(cue), text.index(cue) + len(cue), cue)
            links.append(Link(lid, mid, "r", f"m{target}", "r", "CORRECTS", 1,
                              reference_span=(span.start, span.end)))
            certificates.append(CorrectionEvidence(lid, span))
            chosen[mid] = lid
        prerequisite = support_parents[i]
        if prerequisite is not None:
            span = EvidenceSpan(text.index(dependency), text.index(dependency) + len(dependency), dependency)
            support.append(SupportDependency(cid, f"c{prerequisite}", (span,)))
    problem = Problem("2024-12-31", tuple(sources), tuple(mentions), tuple(links),
                      "independently_authored_finite_correction_audit")
    policy = CorrectionPolicy(source_authorities=tuple(authorities),
                              correction_evidence=tuple(certificates),
                              support_dependencies=tuple(support))
    return problem, readings, chosen, claims, policy


def audit_objective():
    """Exercise every score category with authored nonunit weights/penalties."""
    p, ids, lids, claims, policy = fixture((None, None, 0, None), (None,) * 4)
    ids["m3"] = "u"
    mentions = []
    for i, mention in enumerate(p.mentions):
        readings = (replace(mention.readings[0], unary_score=i + 1, violations=("reading",)),
                    replace(mention.readings[1], unary_score=7, violations=("reading",)))
        mentions.append(replace(mention, readings=readings, null_score=.2 if i == 0 else .5))
    correction = replace(p.links[0], score=4, violations=("correction",))
    inactive = Link("inactive", "m1", "r", "m0", "r", "RESTATES", 5, violations=("inactive",))
    active = Link("active", "m4", "r", "m2", "r", "RESTATES", 6, violations=("active",))
    mentions.append(Mention("m4", "s3", 0, len(p.sources[3].text), (
        Reading("r", ("Org", "CEO", ""), "A", 5, violations=("reading",)),
        Reading("u", None, None, 0))))
    claims["m4", "r"] = replace(claims["m2", "r"], claim_id="c4", source_id="s3", reported_at="2024-01-23")
    ids["m4"], lids["m1"], lids["m4"] = "r", "inactive", "active"
    p = replace(p, mentions=tuple(mentions), links=(correction, inactive, active), beta=2,
                penalties=tuple(Penalty(name, weight) for name, weight in
                                (("reading", .1), ("correction", .4), ("inactive", .3), ("active", .2))))
    memory = materialize_corrected_selection(p, ids, lids, claims, policy)
    objective = evaluate_assignment(p, ids, lids, state_semantics=make_correction_state_semantics(p, claims, policy))
    result = SimpleNamespace(reading_ids=tuple(ids.items()), link_ids=tuple(lids.items()), objective=objective)
    parts = _objective_parts(SimpleNamespace(problem=p, claims=claims), result, memory)
    # Explicit arithmetic, independent of the implementation's bucket iteration.
    expected = {"active_claim_unary_net": 9.7, "inactive_claim_unary_net": .9,
                "unresolved_unary_net": 6.9, "active_temporal_link_net": 11.8,
                "inactive_temporal_link_net": 9.7, "correction_link_net": 7.6,
                "null_link_score": .7, "total": 47.3}
    for name, value in expected.items():
        assert isclose(parts[name], value, abs_tol=1e-12), (name, value, parts[name])
    return expected


def run():
    checks, cases, empty_ledgers, absence_cases = 0, 0, 0, 0
    parents = tuple(product(*(tuple([None] + list(range(i))) for i in range(4))))
    for corrections in parents:
        correction_edges = {(i, target) for i, target in enumerate(corrections) if target is not None}
        for dependencies in parents:
            support_edges = {(i, target) for i, target in enumerate(dependencies) if target is not None}
            args = fixture(corrections, dependencies)
            expected = brute_active(range(4), correction_edges, support_edges)
            memory = materialize_corrected_selection(*args)
            observed = {int(claim.claim_id[1:]) for claim in memory.claims}
            assert observed == expected, (corrections, dependencies, expected, observed)
            checks += 1
            assert set(memory.envelopes) == {f"c{i}" for i in expected}
            checks += 1
            for day in range(1, 9):
                query = Question("q", "Who was CEO of Org?", f"2024-01-{day:02d}", args[0].cutoff)
                answer = memory.answer_key(query, ("Org", "CEO", ""), "reported_actual")
                relevant = {i for i in expected if i + 1 <= day < 8}
                assert answer.status == ("answered" if relevant else "abstain")
                assert set(answer.evidence_assertion_ids) == {f"c{i}" for i in relevant}
                checks += 2
            empty_ledgers += not expected
            cases += 1

    # No correction is selected in these cases. A required reading may be null;
    # the maximal support-closed subset still exists and inference stays feasible.
    for dependencies in parents:
        support_edges = {(i, target) for i, target in enumerate(dependencies) if target is not None}
        for bits in product((False, True), repeat=4):
            args = list(fixture((None,) * 4, dependencies))
            selected = {i for i, bit in enumerate(bits) if bit}
            args[1] = {f"m{i}": "r" if i in selected else "u" for i in range(4)}
            expected = brute_active(selected, set(), support_edges)
            memory = materialize_corrected_selection(*args)
            assert {int(claim.claim_id[1:]) for claim in memory.claims} == expected
            checks += 1
            absence_cases += 1

    # The old decoder rejects a RESTATES exact-date disagreement before a bool
    # extension can run. With replacement, the target and incident equality have
    # to disappear before any global temporal feasibility check.
    p, readings, links, claims, policy = fixture((None, None, 0, None), (None,) * 4)
    readings["m3"] = "u"
    same = Link("same", "m1", "r", "m0", "r", "RESTATES", 1)
    links["m1"] = "same"
    p = replace(p, links=p.links + (same,), mentions=tuple(
        replace(m, readings=(replace(m.readings[0], effective_start=f"2024-01-{i + 1:02d}"), m.readings[1]))
        for i, m in enumerate(p.mentions)))
    assert evaluate_assignment(p, readings, links) is None
    kernel = make_correction_state_semantics(p, claims, policy)
    assert evaluate_assignment(p, readings, links, state_semantics=kernel) == 5
    memory = materialize_corrected_selection(p, readings, links, claims, policy)
    assert {claim.claim_id for claim in memory.claims} == {"c1", "c2"}
    assert memory.diagnostics["correction_ledger"]["inactive_temporal_link_ids"] == ["same"]
    checks += 4

    # A same-episode peer remains unless explicit essential provenance marks it
    # as a derivative. This checks the distinction with the identical link graph.
    dependency_quote = "Copy relies on the earlier record"
    text = p.sources[1].text
    offset = text.index(dependency_quote)
    dependent_policy = replace(policy, support_dependencies=(SupportDependency(
        "c1", "c0", (EvidenceSpan(offset, offset + len(dependency_quote), dependency_quote),)),))
    dependent_memory = materialize_corrected_selection(p, readings, links, claims, dependent_policy)
    assert {claim.claim_id for claim in dependent_memory.claims} == {"c2"}
    checks += 1

    # Contradictory sibling replacements are retained without latest-wins.
    p, readings, links, claims, policy = fixture((None, 0, 0, None), (None,) * 4)
    readings["m3"] = "u"
    changed = {"m1": "B", "m2": "C"}
    p = replace(p, mentions=tuple(replace(m, readings=(
        replace(m.readings[0], value=changed.get(m.mention_id, "A")), m.readings[1])) for m in p.mentions))
    claims = {pair: replace(claim, value=changed.get(pair[0], "A")) for pair, claim in claims.items()}
    memory = materialize_corrected_selection(p, readings, links, claims, policy)
    prediction = memory.answer_key(Question("q", "Who was CEO of Org?", "2024-01-05", p.cutoff),
                                   ("Org", "CEO", ""), "reported_actual")
    assert prediction.status == "indeterminate"
    assert prediction.evidence_assertion_ids == ("c1", "c2")
    checks += 2

    # A later contradicting source does not acquire replacement authority.
    wrong_authority = replace(policy, source_authorities=tuple(
        replace(authority, authority_id="unrelated") if authority.source_id == "s1" else authority
        for authority in policy.source_authorities))
    try:
        materialize_corrected_selection(p, readings, links, claims, wrong_authority)
    except TemporalInfeasible:
        checks += 1
    else:
        raise AssertionError("Nonmatching authority correction was accepted")

    partial = replace(policy, correction_evidence=(replace(policy.correction_evidence[0], coverage="start_only"),
                                                   policy.correction_evidence[1]))
    try:
        materialize_corrected_selection(p, readings, links, claims, partial)
    except ValueError:
        checks += 1
    else:
        raise AssertionError("Unsupported partial correction was widened")

    # Removing a rival because its prerequisite was left unresolved depends on
    # the context used for that unresolved choice, not only the rival's source.
    p, ids, lids, claims, policy = fixture((None,) * 4, (None, 0, None, None))
    ids["m0"], ids["m3"] = "u", "u"
    mentions = list(p.mentions)
    mentions[0] = replace(mentions[0], readings=(mentions[0].readings[0],
        replace(mentions[0].readings[1], context_source_ids=("identity_context",))))
    mentions[1] = replace(mentions[1], readings=(replace(mentions[1].readings[0], value="B"),
                                                 mentions[1].readings[1]))
    sources = list(p.sources)
    sources[1] = replace(sources[1], text=sources[1].text.replace("A served", "B served"))
    claims["m1", "r"] = replace(claims["m1", "r"], value="B", evidence_spans=(
        EvidenceSpan(0, len(sources[1].text), sources[1].text),))
    p = replace(p, mentions=tuple(mentions), sources=tuple(sources) + (
        Source("identity_context", "Context informing the unresolved prerequisite identity.", "2024-01-19"),))
    memory = materialize_corrected_selection(p, ids, lids, claims, policy)
    prediction = memory.answer_key(Question("q", "Who was CEO of Org?", "2024-01-04", p.cutoff),
                                   ("Org", "CEO", ""), "reported_actual")
    assert prediction.values == ("A",)
    assert prediction.evidence_assertion_ids == ("c2",)
    assert {"s0", "identity_context"} <= set(prediction.evidence_source_ids)
    checks += 3

    objective_probe = audit_objective()
    checks += len(objective_probe)

    return {
        "audit": "independent_finite_correction_ledger_oracle_v0.5",
        "evidence_level": "synthetic_implementation_check_not_natural_accuracy",
        "oracle": "enumerate_all_16_subsets_then_choose_the_unique_greatest_admissible_subset",
        "correction_dependency_dag_pairs": cases,
        "support_dag_missing_reading_cases": absence_cases,
        "targeted_checks": 12,
        "weighted_objective_decomposition_checks": len(objective_probe),
        "weighted_objective_decomposition_expected": objective_probe,
        "empty_active_ledgers_in_dag_pairs": empty_ledgers,
        "checks_passed": checks,
        "mismatches": 0,
        "limitations": [
            "Four assertions; one correction and one essential dependency per dependent; chronological DAGs.",
            "Conditional on authored authority, target, whole-assertion scope and support declarations.",
            "Finite coverage does not prove correctness for arbitrary graphs or source entailment.",
            "No natural gold, model extraction, model scores or benchmark outcomes used."
        ]
    }


if __name__ == "__main__":
    result = run()
    output = ROOT / "results" / "correction_finite_audit_v05.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

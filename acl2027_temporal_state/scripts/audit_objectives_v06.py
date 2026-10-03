#!/usr/bin/env python3
"""Independent finite/arithmetic objective audit; synthetic inputs only.

The oracle uses a baseline-plus-margins equation and enumerated support-closed
subsets. It never uses the scorer's breakdown to compute an expected score.
No natural annotations, questions, labels, or results are read.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import product
import json
from math import isclose
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.bounded import EvidenceSpan, TemporalClaim
from temporal_state.corrections import (
    CorrectionEvidence, CorrectionPolicy, SourceAuthority, SupportDependency,
    make_correction_state_semantics,
)
from temporal_state.decoder import (
    Link, Mention, Penalty, Problem, Reading, decode_independent,
    decode_iterative, decode_joint, evaluate_assignment,
)
from temporal_state.models import Source
from temporal_state.objectives import make_objective_function


def span(text, quote):
    start = text.index(quote)
    return EvidenceSpan(start, start + len(quote), quote)


def fixture(scores, dependent=False):
    sources, mentions, claims, authorities = [], [], {}, []
    for i, score in enumerate(scores):
        text = "Org says Ada was CEO; corrects earlier report; relies on earlier report."
        source = Source(f"s{i}", text, f"2024-02-0{i+1}")
        sources.append(source)
        mentions.append(Mention(f"m{i}", source.source_id, 0, len(text), (
            Reading("r", ("Org", "CEO", ""), "Ada", score, violations=("reading",)),
            Reading("u0", None, None, 0),
            Reading("u1", None, None, -.75, violations=("unresolved",)),
        ), null_score=(.25, -.5, .75)[i]))
        claims[f"m{i}", "r"] = TemporalClaim(
            f"c{i}", source.source_id, "Org", "CEO", "Ada", reported_at=source.available_at,
            evidence_spans=(span(text, "Ada was CEO"),))
        authorities.append(SourceAuthority(source.source_id, "Org", (span(text, "Org"),)))
    cue = span(sources[1].text, "corrects earlier report")
    links = (
        Link("correction", "m1", "r", "m0", "r", "CORRECTS", 2,
             reference_span=(cue.start, cue.end), violations=("correction",)),
        Link("restatement", "m2", "r", "m0", "r", "RESTATES", 1,
             violations=("temporal",)),
    )
    problem = Problem("2024-12-31", tuple(sources), tuple(mentions), links,
        "independently authored finite objective audit", beta=2,
        penalties=(Penalty("reading", .5), Penalty("unresolved", .25),
                   Penalty("correction", .5), Penalty("temporal", .25)))
    dependencies = ((SupportDependency("c2", "c0",
                       (span(sources[2].text, "relies on earlier report"),)),)
                    if dependent else ())
    policy = CorrectionPolicy(source_authorities=tuple(authorities),
        correction_evidence=(CorrectionEvidence("correction", cue),),
        support_dependencies=dependencies)
    return problem, claims, policy


def active_subset(ids, correction, dependent):
    """Enumerate sets satisfying a specification, not a withdrawal recurrence."""
    selected = {i for i, rid in enumerate(ids) if rid == "r"}
    feasible = []
    for bits in product((False, True), repeat=3):
        candidate = {i for i, bit in enumerate(bits) if bit}
        if not candidate <= selected or (correction and 0 in candidate):
            continue
        if dependent and 2 in candidate and 0 not in candidate:
            continue
        feasible.append(candidate)
    maximal = [candidate for candidate in feasible
               if all(other <= candidate for other in feasible)]
    assert len(maximal) == 1
    return maximal[0]


def candidate_states(dependent):
    for ids in product(("r", "u0", "u1"), repeat=3):
        for correct, restate in product((False, True), repeat=2):
            if correct and (ids[0] != "r" or ids[1] != "r"):
                continue
            if restate and (ids[0] != "r" or ids[2] != "r"):
                continue
            yield ids, correct, restate, active_subset(ids, correct, dependent)


def margin_oracle(scores, ids, correction, restatement, active, mode):
    """Closed arithmetic specified independently for this weighted fixture.

    Baseline is the three best unresolved references (zero) and the three null
    link scores (sum 0.5). Resolved margins are score-0.5; second unresolved
    margins are -1. Correction gain above outgoing null is 4, temporal gain 1.
    """
    charged = {i for i, rid in enumerate(ids) if rid == "r"}
    if mode == "active_support":
        charged &= active
    resolved = sum(scores[i] - .5 for i in charged)
    unresolved = -sum(rid == "u1" for rid in ids)
    correction_margin = 4 if correction else 0
    temporal_margin = int(restatement and (mode == "historical" or {0, 2} <= active))
    return .5 + resolved + unresolved + correction_margin + temporal_margin


def selected_parts(problem, ids, correct, restate):
    selected = {m.mention_id: next(r for r in m.readings if r.reading_id == rid)
                for m, rid in zip(problem.mentions, ids)}
    chosen = (None, problem.links[0] if correct else None,
              problem.links[1] if restate else None)
    link_ids = {m.mention_id: link.link_id if link else None
                for m, link in zip(problem.mentions, chosen)}
    return selected, chosen, link_ids


def shifted(problem, kind):
    offsets = (7, -5, 11)
    if kind == "unary":
        return replace(problem, mentions=tuple(replace(m, readings=tuple(
            replace(r, unary_score=r.unary_score + offset) for r in m.readings))
            for m, offset in zip(problem.mentions, offsets))), sum(offsets)
    by_mid = {m.mention_id: offset for m, offset in zip(problem.mentions, offsets)}
    return replace(problem,
        mentions=tuple(replace(m, null_score=m.null_score + by_mid[m.mention_id])
                       for m in problem.mentions),
        links=tuple(replace(link, score=link.score + by_mid[link.mention_id] / problem.beta)
                    for link in problem.links)), sum(offsets)


def counterexamples():
    """Fixed-reading analytic examples; no answer labels or accuracy claims."""
    # Old resolved score=10, its unresolved reference=0, correcting claim=2,
    # correction reward above null=3. Historical selects correction: 15 > 12.
    # Anchored support suppresses correction: 5 < 12.
    suppression = {"old_margin": 10, "replacement_margin": 2, "correction_gain": 3,
        "historical_no_correction": 12, "historical_correction": 15,
        "active_no_correction": 12, "active_correction": 5}
    assert suppression["historical_correction"] > suppression["historical_no_correction"]
    assert suppression["active_correction"] < suppression["active_no_correction"]
    # Selecting an old negative target can unlock a correction edge while the
    # target's -4 margin disappears. A selected candidate need not be believed.
    gaming = {"old_margin": -4, "replacement_margin": 2, "correction_gain": 3,
        "old_unresolved": 2, "historical_correction": 1, "active_correction": 5}
    assert gaming["historical_correction"] < gaming["old_unresolved"]
    assert gaming["active_correction"] > gaming["old_unresolved"]
    # Raw zeroing: old score 1, replacement score 2, correction reward 3.
    # A +10 offset to every old-reading score changes the preferred state;
    # anchored scores all increase by exactly 10 and preserve the ordering.
    zeroing = {"before": {"no_correction": 3, "correction": 5},
               "after_old_unary_offset_10": {"no_correction": 13, "correction": 5},
               "anchored_after": {"no_correction": 13, "correction": 15}}
    assert zeroing["before"]["correction"] > zeroing["before"]["no_correction"]
    assert zeroing["after_old_unary_offset_10"]["correction"] < zeroing["after_old_unary_offset_10"]["no_correction"]
    assert zeroing["anchored_after"]["correction"] > zeroing["anchored_after"]["no_correction"]
    # Execute the suppression and negative-target examples through production
    # decoding too, with the third fixture mention always unresolved at optimum.
    production = []
    for old_score in (10, -4):
        p, claims, policy = fixture((old_score, 2, -100))
        p = replace(p, beta=1, penalties=(),
            mentions=tuple(replace(m, null_score=0, readings=tuple(
                replace(r, violations=()) for r in m.readings)) for m in p.mentions),
            links=(replace(p.links[0], score=3, violations=()),))
        kernel = make_correction_state_semantics(p, claims, policy)
        observed = {}
        for mode in ("historical", "active_support"):
            result = decode_joint(p, state_semantics=kernel,
                objective_function=make_objective_function(p, mode))
            observed[mode] = {"objective": result.objective,
                "reading_ids": dict(result.reading_ids), "link_ids": dict(result.link_ids)}
        if old_score == 10:
            assert observed["historical"]["objective"] == 15
            assert observed["historical"]["link_ids"]["m1"] == "correction"
            assert observed["active_support"]["objective"] == 12
            assert observed["active_support"]["link_ids"]["m1"] is None
        else:
            assert observed["historical"]["objective"] == 2
            assert observed["historical"]["reading_ids"]["m0"] == "u0"
            assert observed["active_support"]["objective"] == 5
            assert observed["active_support"]["reading_ids"]["m0"] == "r"
            assert observed["active_support"]["link_ids"]["m1"] == "correction"
        production.append({"old_score": old_score, "decoding": observed})
    return {"valid_correction_suppression": suppression,
            "negative_target_selection": gaming, "raw_zeroing_offset_failure": zeroing,
            "production_counterexample_replays": production}


def pipeline_checks():
    """Exercise objective/cache binding independently of pipeline unit fixtures."""
    from temporal_state.correction_io import make_correction_cache
    from temporal_state.correction_pipeline import decode_correction_cache
    from temporal_state.models import Question
    from temporal_state.objective_pipeline import answer_objective, decode_objective
    from temporal_state.scored_io import make_cache
    p, claims, policy = fixture((1, 1, 1), True)
    provenance = {"evidence_type": "authored_diagnostic", "candidate_model_id": None,
        "candidate_model_revision": None, "scorer_model_id": None,
        "scorer_model_revision": None, "prompt_sha256": None,
        "config_sha256": "a" * 64, "score_definition": p.score_provenance}
    cache = make_correction_cache(make_cache(p, claims, [], provenance), policy)
    question = Question("synthetic_pipeline_query", "Who was CEO of Org?", "2024-02-01", p.cutoff)
    checks, runs = 0, 0
    identities = set()
    for mode in ("historical", "active_support"):
        for method in ("independent", "iterative", "iterative_restarts", "joint_exact"):
            state = decode_objective(cache, method, objective_mode=mode, restarts=4, seed=37)
            assert state.memory.diagnostics["objective_ablation"]["total"] == state.result.objective
            prediction = answer_objective(cache, state, question, use_aliases=False)
            assert prediction.diagnostics["objective_identity_sha256"] == state.objective_digest
            identities.add(state.objective_digest)
            checks += 2
            runs += 1
            if mode == "historical":
                old = decode_correction_cache(cache, method, restarts=4, seed=37)
                assert old.result.objective == state.result.objective
                assert old.result.reading_ids == state.result.reading_ids
                assert old.result.link_ids == state.result.link_ids
                checks += 3
                runs += 1
            for invalid in (replace(state, objective_mode=("active_support" if mode == "historical" else "historical")),
                            replace(state, objective_digest="0" * 64)):
                try:
                    answer_objective(cache, invalid, question, use_aliases=False)
                except ValueError:
                    checks += 1
                else:
                    raise AssertionError("Cross-objective or stale objective identity accepted")
    assert len(identities) == 2
    checks += 1
    return {"checks": checks, "decoder_runs": runs,
            "distinct_objective_identities": len(identities),
            "historical_pipeline_preserved": True,
            "mismatched_objective_state_rejected": True}


def run():
    checks, states, grids, method_runs, invariant_states = 0, 0, 0, 0, 0
    for dependent in (False, True):
        for scores in product((-3, 1, 5), repeat=3):
            problem, claims, policy = fixture(scores, dependent)
            kernel = make_correction_state_semantics(problem, claims, policy)
            functions = {mode: make_objective_function(problem, mode)
                         for mode in ("historical", "active_support")}
            shifts = {kind: shifted(problem, kind) for kind in ("unary", "link")}
            shifted_functions = {(kind, mode): make_objective_function(p, mode)
                for kind, (p, _) in shifts.items() for mode in functions}
            expectations = {mode: [] for mode in functions}
            local_ids = tuple(min(m.readings, key=lambda r: (
                -(r.unary_score - sum(p.weight for p in problem.penalties if p.name in r.violations)),
                r.reading_id)).reading_id for m in problem.mentions)
            for ids, correct, restate, active in candidate_states(dependent):
                selected, chosen, link_ids = selected_parts(problem, ids, correct, restate)
                components = kernel(selected, chosen)
                assert components is not None
                assert {int(mid[1:]) for group in components for mid in group} == active
                checks += 2
                id_map = {f"m{i}": rid for i, rid in enumerate(ids)}
                for mode, function in functions.items():
                    expected = margin_oracle(scores, ids, correct, restate, active, mode)
                    observed = evaluate_assignment(problem, id_map, link_ids,
                        state_semantics=kernel, objective_function=function)
                    assert observed == expected, (scores, ids, correct, restate, mode, observed, expected)
                    detail = function.breakdown(selected, chosen, components)
                    assert detail["total"] == expected
                    assert isclose(detail["historical_total"] - expected,
                                   detail["removed_inactive_advantage"], abs_tol=1e-12)
                    checks += 3
                    expectations[mode].append((expected, ids, link_ids))
                    for kind, (p, offset) in shifts.items():
                        shifted_selected, shifted_chosen, _ = selected_parts(p, ids, correct, restate)
                        actual = shifted_functions[kind, mode](shifted_selected, shifted_chosen, components)
                        assert actual == expected + offset
                        checks += 1
                        invariant_states += 1
                states += 1
            for mode, function in functions.items():
                optimum = max(row[0] for row in expectations[mode])
                independent_optimum = max(row[0] for row in expectations[mode] if row[1] == local_ids)
                kwargs = {"state_semantics": kernel, "objective_function": function}
                joint = decode_joint(problem, **kwargs)
                independent = decode_independent(problem, **kwargs)
                iterative = decode_iterative(problem, **kwargs)
                restarted = decode_iterative(problem, restarts=4, seed=37, **kwargs)
                assert joint.objective == optimum
                assert independent.objective == independent_optimum
                assert independent.objective <= iterative.objective <= restarted.objective <= optimum
                checks += 3
                for result in (joint, independent, iterative, restarted):
                    assert evaluate_assignment(problem, dict(result.reading_ids), dict(result.link_ids), **kwargs) == result.objective
                    checks += 1
                    method_runs += 1
                for kind, (p, offset) in shifts.items():
                    shifted_kernel = make_correction_state_semantics(p, claims, policy)
                    transformed = decode_joint(p, state_semantics=shifted_kernel,
                        objective_function=shifted_functions[kind, mode])
                    assert transformed.objective == joint.objective + offset
                    assert transformed.reading_ids == joint.reading_ids
                    assert transformed.link_ids == joint.link_ids
                    checks += 3
                    method_runs += 1
            grids += 1
    examples = counterexamples()
    checks += 16
    method_runs += 4
    pipeline = pipeline_checks()
    checks += pipeline["checks"]
    method_runs += pipeline["decoder_runs"]
    report = {
        "version": "0.6", "audit_type": "independent synthetic finite and arithmetic oracle",
        "natural_inputs_or_labels_read": False, "score_grids": grids,
        "feasible_assignments": states, "gauge_invariance_state_comparisons": invariant_states,
        "decoder_runs": method_runs, "checks": checks, "mismatches": 0,
        "oracle": "enumerated support-closed subsets plus independently specified baseline/margin arithmetic",
        "counterexamples": examples,
        "pipeline_audit": pipeline,
        "scope": "finite implementation verification; no extraction, calibration, natural accuracy, or algorithmic novelty claim",
    }
    path = ROOT / "results/objective_audit_v06.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    run()

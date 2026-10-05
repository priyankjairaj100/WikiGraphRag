"""Observed typed-fact bindings for the v22 development comparison.

Candidates are resolved normalized facts from the v15.1 reader. A quantity that
does not occur in those facts is never proposed. A fact that fails a specified
aspect is outside the question. It is not used as a refutation.

Answer classes keep the full reported binding. The same number under two
scopes stays two classes. Coverage, when asserted, covers only this fact list.
Prose, unresolved facts, and any other filing are outside the gate.

The sufficiency baseline emits one supported class while rivals remain.
Aspect filtering and the certificate abstain in that case. Neither decision
is a natural-question score.
"""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Dict, Optional, Tuple

from temporal_state.certificate_controller_v22 import (
    Judgment, Obligation, run_acquisition,
)
from temporal_state.certificate_v21 import (
    Action, Candidate, Cost, CoverageAssertion, Problem,
)


ASPECTS = ("concept", "entity", "period", "unit", "dimensions")
COVERAGE_PROVENANCE = (
    "Closed over resolved normalized typed-reader facts matching the specified "
    "aspects. Prose, unresolved facts, hidden markup, and other filings are outside this gate."
)
COST_UNIT = "typed_facts"
COST_PROVENANCE = "One charge per acquired typed fact."


class BindingError(ValueError):
    """The document or query is outside the typed-fact contract."""


@dataclass(frozen=True)
class AnswerClass:
    candidate_id: str
    normalized_value: str
    fact_ordinals: Tuple[int, ...]
    binding_key: str


@dataclass(frozen=True)
class BindingWorld:
    problem: Problem
    obligations: Tuple[Obligation, ...]
    classes: Tuple[AnswerClass, ...]
    action_ids: Tuple[str, ...]
    described_facts: int


@dataclass(frozen=True)
class PolicyDecision:
    policy: str
    outcome: str
    candidate_id: Optional[str]
    acquired_facts: int
    described_facts: int
    rivals_unresolved: int


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _class_id(binding_key, normalized_value):
    digest = hashlib.sha256((binding_key + "\n" + normalized_value).encode("utf-8")).hexdigest()
    return "class-" + digest[:20]


def eligible_facts(document, *, include_hidden=False):
    """Resolved normalized facts. Hidden markup stays out of the primary list."""
    if not isinstance(document, dict) or not isinstance(document.get("facts"), list):
        raise BindingError("document must be a typed-reader result with a fact list")
    kept, hidden, unresolved = [], 0, 0
    for fact in document["facts"]:
        if not isinstance(fact, dict):
            raise BindingError("fact record must be an object")
        if fact.get("binding_status") != "reported_aspects_resolved" or fact.get("status") != "normalized":
            unresolved += 1
            continue
        if not isinstance(fact.get("resolved_aspects"), dict) or not all(fact["resolved_aspects"].get(key) for key in ASPECTS):
            unresolved += 1
            continue
        if fact.get("visibility") == "hidden_markup" and not include_hidden:
            hidden += 1
            continue
        if not isinstance(fact.get("normalized_value"), str) or not isinstance(fact.get("fact_ordinal"), int):
            raise BindingError("eligible fact is missing an ordinal or normalized value")
        aspects = fact.get("reported_aspects")
        if not isinstance(aspects, dict) or any(key not in aspects for key in ASPECTS):
            raise BindingError("eligible fact is missing a reported aspect")
        kept.append(fact)
    return tuple(kept), hidden, unresolved


def _matches(fact, specified):
    if not isinstance(specified, dict) or any(key not in ASPECTS for key in specified):
        raise BindingError("specified aspects must be a subset of the reported aspect names")
    aspects = fact["reported_aspects"]
    return all(aspects[key] == required for key, required in specified.items())


def build_world(document, specified, *, coverage_cleared=True, include_hidden=False) -> BindingWorld:
    """Build one filing's observed candidates. Zero matches produce no problem."""
    if not isinstance(coverage_cleared, bool):
        raise BindingError("coverage_cleared must be a bool")
    facts, _, _ = eligible_facts(document, include_hidden=include_hidden)
    matched = [fact for fact in facts if _matches(fact, specified)]
    grouped: Dict[str, list] = {}
    order = []
    for fact in matched:
        aspects = fact["reported_aspects"]
        binding_key = _canonical({key: aspects[key] for key in ASPECTS})
        identity = binding_key + "\n" + fact["normalized_value"]
        if identity not in grouped:
            order.append(identity)
            grouped[identity] = []
        grouped[identity].append(fact)
    classes = []
    atoms, actions, candidates, obligations, action_ids = [], [], [], [], []
    for identity in order:
        members = grouped[identity]
        binding_key, normalized_value = identity.split("\n", 1)
        candidate_id = _class_id(binding_key, normalized_value)
        classes.append(AnswerClass(candidate_id, normalized_value,
                                    tuple(fact["fact_ordinal"] for fact in members), binding_key))
        candidates.append(Candidate(candidate_id, candidate_id, ("scoped-answer",)))
        for fact in members:
            ordinal = fact["fact_ordinal"]
            fact_atoms = tuple(f"f{ordinal}/{name}" for name in ("value", *ASPECTS))
            atoms.extend(fact_atoms)
            action_id = f"fact-{ordinal}"
            actions.append(Action(action_id, fact_atoms))
            action_ids.append(action_id)
            obligations.append(Obligation(f"support-{ordinal}", candidate_id, "scoped-answer", fact_atoms))
    if not classes:
        raise BindingError("no observed fact matches the specified aspects")
    if len({item.candidate_id for item in classes}) != len(classes):
        raise BindingError("answer-class identifiers collided")
    problem = Problem(tuple(atoms), tuple(candidates), tuple(actions), (),
                      CoverageAssertion(coverage_cleared, COVERAGE_PROVENANCE),
                      "Typed-reader fact ordinals and reported aspects.",
                      "Observed typed facts only; no invented quantities.")
    return BindingWorld(problem, tuple(obligations), tuple(classes), tuple(action_ids), len(matched))


def fact_cost(selected, received):
    return Cost(len(selected), COST_UNIT, COST_PROVENANCE)


def tool_verifier(visible, pending):
    """The acquired fact is its own recorded binding. Absence stays unasked."""
    return tuple(Judgment(item.obligation_id, "+") for item in pending)


def _planner(action_ids, limit):
    def planner(visible):
        if len(visible.selected_action_ids) >= limit:
            return ()
        remaining = tuple(action_id for action_id in action_ids
                          if not any(view.action_id == action_id and view.acquired for view in visible.actions))
        return remaining[:1]

    return planner


def _decision(policy, outcome, world, candidate_id, acquired):
    rivals = 0 if candidate_id is None else max(0, len(world.classes) - 1)
    if outcome != "emitted":
        rivals = max(0, len(world.classes) - (1 if candidate_id else 0))
    return PolicyDecision(policy, outcome, candidate_id, acquired, world.described_facts, rivals)


def compare_policies(world: BindingWorld, *, certificate_limit=1) -> Tuple[PolicyDecision, ...]:
    """Same candidates and facts. Only the decision rule changes."""
    if not isinstance(certificate_limit, int) or isinstance(certificate_limit, bool) or certificate_limit < 1:
        raise BindingError("certificate_limit must be a positive integer")
    described = world.described_facts
    if len(world.classes) == 1:
        aspect = PolicyDecision("aspect_filter", "emitted", world.classes[0].candidate_id, 1, described, 0)
    else:
        aspect = PolicyDecision("aspect_filter", "abstained", None, 0, described, len(world.classes))
    sufficiency = PolicyDecision("sufficiency", "emitted", world.classes[0].candidate_id, 1, described,
                                 max(0, len(world.classes) - 1))
    limit = min(certificate_limit, len(world.action_ids))
    result = run_acquisition(world.problem, world.obligations, _planner(world.action_ids, limit),
                             tool_verifier, fact_cost, max_steps=limit)
    if result.authorized:
        chosen = result.evaluation.certificate_candidates
        if len(chosen) != 1:
            raise BindingError("certificate authorized more than one class")
        certificate = _decision("certificate", "emitted", world, chosen[0], len(result.acquisition_order))
    else:
        certificate = PolicyDecision("certificate", "abstained", None, len(result.acquisition_order),
                                     described, len(world.classes))
    return (aspect, sufficiency, certificate)


def summarize_document(document, *, source_version):
    """Count binding disagreements inside one filing. This is not a QA score."""
    facts, hidden, unresolved = eligible_facts(document)
    by_concept: Dict[str, list] = {}
    for fact in facts:
        by_concept.setdefault(_canonical(fact["reported_aspects"]["concept"]), []).append(fact)
    multi = unique = conflicts = 0
    underspecified_residual = underspecified_checked = 0
    specified_agreement = specified_checked = 0
    for concept_key, members in by_concept.items():
        concept = members[0]["reported_aspects"]["concept"]
        world = build_world(document, {"concept": concept})
        values_per_binding: Dict[str, set] = {}
        for item in world.classes:
            values_per_binding.setdefault(item.binding_key, set()).add(item.normalized_value)
        if any(len(values) > 1 for values in values_per_binding.values()):
            conflicts += 1
        if len(world.classes) == 1:
            unique += 1
            continue
        multi += 1
        underspecified_checked += 1
        aspect, sufficiency, certificate = compare_policies(world, certificate_limit=1)
        if aspect.outcome == "abstained" and sufficiency.outcome == "emitted" and certificate.outcome == "abstained":
            underspecified_residual += 1
        representative = members[0]
        specified = {key: representative["reported_aspects"][key] for key in ASPECTS}
        pinned = build_world(document, specified)
        specified_checked += 1
        pinned_decisions = compare_policies(pinned, certificate_limit=1)
        if len(pinned.classes) == 1 and all(item.outcome == "emitted" and item.candidate_id == pinned.classes[0].candidate_id
                                            for item in pinned_decisions):
            specified_agreement += 1
    return {
        "source_sha256": document.get("source_sha256"),
        "source_version": source_version,
        "eligible_facts": len(facts),
        "excluded_hidden": hidden,
        "excluded_unresolved_or_invalid": unresolved,
        "concepts": len(by_concept),
        "unique_binding_concepts": unique,
        "multi_class_concepts": multi,
        "same_binding_value_conflicts": conflicts,
        "underspecified_concepts_checked": underspecified_checked,
        "underspecified_residual": underspecified_residual,
        "fully_specified_checks": specified_checked,
        "fully_specified_all_policies_agree": specified_agreement,
        "cross_filing_comparisons": 0,
        "natural_questions": 0,
    }


def _qname_local(value):
    if not isinstance(value, str) or not value.startswith("{") or "}" not in value:
        return value
    return value[1:].split("}", 1)[1]


def _binding_key(aspects, mode):
    if mode == "exact":
        return _canonical({key: aspects[key] for key in ASPECTS})
    if mode != "local_name_diagnostic":
        raise BindingError("binding mode must be exact or local_name_diagnostic")
    unit = aspects["unit"] if isinstance(aspects["unit"], dict) else {}
    dimensions = []
    for item in aspects["dimensions"]:
        dimensions.append((_qname_local(item.get("dimension")), _qname_local(item.get("member")),
                           item.get("kind"), item.get("placement")))
    return _canonical({
        "concept": _qname_local(aspects["concept"]),
        "entity": aspects["entity"],
        "period": aspects["period"],
        "unit_shape": unit.get("shape"),
        "unit_locals": [_qname_local(item) for item in unit.get("measures", [])],
        "unit_numerator_locals": [_qname_local(item) for item in unit.get("numerator_measures", [])],
        "unit_denominator_locals": [_qname_local(item) for item in unit.get("denominator_measures", [])],
        "dimensions": dimensions,
    })


def _value_index(document, mode):
    facts, _, _ = eligible_facts(document)
    index: Dict[str, set] = {}
    concepts = set()
    for fact in facts:
        key = _binding_key(fact["reported_aspects"], mode)
        index.setdefault(key, set()).add(fact["normalized_value"])
        concepts.add(_canonical(fact["reported_aspects"]["concept"]))
    return index, concepts


def conflict_profile(document):
    """Classify same-binding numeric disagreements. Values are not returned."""
    index, concepts = _value_index(document, "exact")
    buckets = {"within_0.1pct": 0, "within_1pct": 0, "within_5pct": 0, "larger": 0, "zero_involved": 0}
    scale_differs = 0
    conflict_concepts = set()
    facts, _, _ = eligible_facts(document)
    grouped: Dict[str, list] = {}
    for fact in facts:
        grouped.setdefault(_binding_key(fact["reported_aspects"], "exact"), []).append(fact)
    for key, members in grouped.items():
        values = {fact["normalized_value"] for fact in members}
        if len(values) < 2:
            continue
        conflict_concepts.add(_canonical(members[0]["reported_aspects"]["concept"]))
        if len({fact.get("scale_property") for fact in members}) > 1:
            scale_differs += 1
        magnitudes = sorted(abs(Decimal(value)) for value in values if Decimal(value) != 0)
        if len(magnitudes) < 2:
            buckets["zero_involved"] += 1
            continue
        gap = magnitudes[-1] / magnitudes[0] - 1
        if gap <= Decimal("0.001"):
            buckets["within_0.1pct"] += 1
        elif gap <= Decimal("0.01"):
            buckets["within_1pct"] += 1
        elif gap <= Decimal("0.05"):
            buckets["within_5pct"] += 1
        else:
            buckets["larger"] += 1
    return {
        "bindings": len(index),
        "concepts": len(concepts),
        "conflict_bindings": sum(buckets.values()),
        "conflict_concepts": len(conflict_concepts),
        "scale_attribute_differs": scale_differs,
        "ratio_buckets": buckets,
        "values_recorded": False,
    }


def compare_filings(first, second, mode):
    """Count shared bindings. local_name_diagnostic is not an admitted join."""
    left, _ = _value_index(first, mode)
    right, _ = _value_index(second, mode)
    shared = set(left) & set(right)
    same = 0
    for key in shared:
        if left[key] == right[key] and len(left[key]) == 1:
            same += 1
    return {
        "mode": mode,
        "admitted_join": mode == "exact",
        "bindings_first": len(left),
        "bindings_second": len(right),
        "shared": len(shared),
        "same_singleton_value": same,
        "not_same_value": len(shared) - same,
        "only_first": len(set(left) - set(right)),
        "only_second": len(set(right) - set(left)),
        "values_recorded": False,
    }

"""Conditional, atomic support replacement before temporal materialization.

This layer changes which assertion supports are active, never their event dates.
Certificates are source-grounded extraction declarations, not semantic proofs.
"""
from dataclasses import dataclass, replace
from typing import Mapping

from .bounded import EvidenceSpan, TemporalClaim, claim_to_dict
from .coupled import CoupledMemory, TemporalInfeasible, _prepare, materialize_selection
from .decoder import Problem


@dataclass(frozen=True)
class SourceAuthority:
    source_id: str
    authority_id: str
    evidence_spans: tuple[EvidenceSpan, ...]


@dataclass(frozen=True)
class CorrectionEvidence:
    link_id: str
    reference_span: EvidenceSpan
    action: str = "replace"
    coverage: str = "whole_assertion"


@dataclass(frozen=True)
class SupportDependency:
    dependent_claim_id: str
    prerequisite_claim_id: str
    evidence_spans: tuple[EvidenceSpan, ...]


@dataclass(frozen=True)
class CorrectionPolicy:
    version: str = "0.5"
    source_authorities: tuple[SourceAuthority, ...] = ()
    correction_evidence: tuple[CorrectionEvidence, ...] = ()
    support_dependencies: tuple[SupportDependency, ...] = ()


def _unique(values, name):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {name}")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def _evidence(spans, source, name):
    if not isinstance(spans, tuple) or not spans:
        raise ValueError(f"{name} must be a nonempty immutable tuple")
    for span in spans:
        if (not isinstance(span, EvidenceSpan) or type(span.start) is not int
                or type(span.end) is not int or not isinstance(span.quote, str)
                or not 0 <= span.start < span.end <= len(source.text)
                or source.text[span.start:span.end] != span.quote):
            raise ValueError(f"{name} must match exact source slices")


def _acyclic(graph):
    complete, visiting = set(), set()

    def visit(node):
        if node in visiting:
            return False
        if node in complete:
            return True
        visiting.add(node)
        if any(not visit(parent) for parent in graph.get(node, ())):
            return False
        visiting.remove(node)
        complete.add(node)
        return True

    return all(visit(node) for node in graph)


def validate_correction_policy(problem: Problem,
                               claims: Mapping[tuple[str, str], TemporalClaim],
                               policy: CorrectionPolicy) -> None:
    """Validate exact evidence and structure, without certifying entailment.

    Uncertified CORRECTS alternatives can remain in a candidate graph. They are
    infeasible only if selected. Essential dependencies form a candidate DAG.
    """
    prepared = _prepare(problem, claims)
    if not isinstance(policy, CorrectionPolicy) or policy.version != "0.5":
        raise ValueError("Unsupported correction policy version")
    for name in ("source_authorities", "correction_evidence", "support_dependencies"):
        if not isinstance(getattr(policy, name), tuple):
            raise ValueError(f"Policy {name} must be an immutable tuple")
    sources = {source.source_id: source for source in problem.sources}
    by_claim = {claim.claim_id: claim for claim in claims.values()}
    if any(not isinstance(item, SourceAuthority) for item in policy.source_authorities):
        raise ValueError("Source authority records must be typed")
    _unique([item.source_id for item in policy.source_authorities], "source authority")
    for item in policy.source_authorities:
        if item.source_id not in sources:
            raise ValueError("Authority source must belong to the eligible prefix")
        _text(item.authority_id, "authority_id")
        _evidence(item.evidence_spans, sources[item.source_id], "Authority evidence")
    if any(not isinstance(item, CorrectionEvidence) for item in policy.correction_evidence):
        raise ValueError("Correction evidence records must be typed")
    _unique([item.link_id for item in policy.correction_evidence], "correction certificate")
    for item in policy.correction_evidence:
        link = prepared[2].get(item.link_id)
        if link is None or link.relation != "CORRECTS":
            raise ValueError("Correction evidence must name an existing CORRECTS link")
        if item.action != "replace" or item.coverage != "whole_assertion":
            raise ValueError("Only whole-assertion replacement is supported")
        source = sources[prepared[0][link.mention_id].source_id]
        _evidence((item.reference_span,), source, "Correction reference evidence")
        if link.reference_span != (item.reference_span.start, item.reference_span.end):
            raise ValueError("Certificate must reproduce the link's exact reference span")
    if any(not isinstance(item, SupportDependency) for item in policy.support_dependencies):
        raise ValueError("Support dependency records must be typed")
    _unique([(item.dependent_claim_id, item.prerequisite_claim_id)
             for item in policy.support_dependencies], "support dependency")
    graph = {}
    for item in policy.support_dependencies:
        if item.dependent_claim_id not in by_claim or item.prerequisite_claim_id not in by_claim:
            raise ValueError("Support dependencies must name existing candidate claim IDs")
        if item.dependent_claim_id == item.prerequisite_claim_id:
            raise ValueError("A claim cannot depend on itself")
        dependent = by_claim[item.dependent_claim_id]
        prerequisite = by_claim[item.prerequisite_claim_id]
        if sources[prerequisite.source_id].available_at > sources[dependent.source_id].available_at:
            raise ValueError("Essential support cannot come from a later source")
        _evidence(item.evidence_spans, sources[dependent.source_id], "Dependency evidence")
        graph.setdefault(item.dependent_claim_id, set()).add(item.prerequisite_claim_id)
    if not _acyclic(graph):
        raise ValueError("Essential support dependencies must be acyclic")


def _materialize(problem, reading_ids, link_ids, claims, policy, prepared):
    mentions, readings, links = prepared
    if set(reading_ids) != set(mentions) or set(link_ids) != set(mentions):
        raise ValueError("Select exactly one reading and one link or null per mention")
    try:
        selected_readings = {mid: readings[mid, rid] for mid, rid in reading_ids.items()}
    except KeyError as exc:
        raise ValueError("Unknown selected reading") from exc
    selected = {mid: claims[mid, reading_ids[mid]] for mid, reading in selected_readings.items()
                if not reading.unresolved}
    source_map = {source.source_id: source for source in problem.sources}
    authorities = {item.source_id: item for item in policy.source_authorities}
    certificates = {item.link_id: item for item in policy.correction_evidence}
    chosen, correction_links, correction_graph = [], [], {}
    for mid, lid in link_ids.items():
        if lid is None:
            continue
        link = links.get(lid)
        if link is None or link.mention_id != mid:
            raise ValueError("Selected link must belong to its mention")
        if (mid == link.target_mention_id or mid not in selected
                or link.target_mention_id not in selected or reading_ids[mid] != link.reading_id
                or reading_ids[link.target_mention_id] != link.target_reading_id):
            raise TemporalInfeasible("Link endpoints must be distinct selected resolved readings")
        left, right = selected[mid], selected[link.target_mention_id]
        if left.key != right.key:
            raise TemporalInfeasible("Selected link must preserve the exact factual key and scope")
        if link.relation == "RESTATES" and (left.value != right.value or left.polarity != right.polarity):
            raise TemporalInfeasible("RESTATES requires equal value and polarity")
        if link.relation == "CHANGES" and (left.value == right.value
                or left.polarity != "positive" or right.polarity != "positive"):
            raise TemporalInfeasible("CHANGES requires distinct positive values")
        if link.relation == "CORRECTS":
            if link.link_id not in certificates:
                raise TemporalInfeasible("Selected correction lacks an explicit replacement certificate")
            origin = authorities.get(left.source_id)
            target = authorities.get(right.source_id)
            if origin is None or target is None or origin.authority_id != target.authority_id:
                raise TemporalInfeasible("Selected correction requires the same declared asserting authority")
            if source_map[right.source_id].available_at > source_map[left.source_id].available_at:
                raise TemporalInfeasible("Correction target cannot be a later source")
            # A quote is required even on different days: chronology is not a
            # correction cue. Same-day explicit references do not invent times.
            correction_links.append(link)
            correction_graph.setdefault(mid, set()).add(link.target_mention_id)
        chosen.append(link)
    if not _acyclic(correction_graph):
        raise TemporalInfeasible("Selected correction references must be acyclic")

    by_claim = {claim.claim_id: (mid, claim) for mid, claim in selected.items()}
    directly_withdrawn = {selected[link.target_mention_id].claim_id for link in correction_links}
    inactive = set(directly_withdrawn)
    reasons = {cid: {"explicit_replacement"} for cid in directly_withdrawn}
    dependencies = {}
    for item in policy.support_dependencies:
        dependencies.setdefault(item.dependent_claim_id, set()).add(item.prerequisite_claim_id)
    changed = True
    while changed:
        changed = False
        for dependent, prerequisites in sorted(dependencies.items()):
            if dependent not in by_claim:
                continue
            missing = prerequisites - set(by_claim)
            withdrawn = prerequisites & inactive
            if missing or withdrawn:
                reasons.setdefault(dependent, set()).update(
                    {f"missing_prerequisite:{cid}" for cid in missing}
                    | {f"inactive_prerequisite:{cid}" for cid in withdrawn})
                if dependent not in inactive:
                    inactive.add(dependent)
                    changed = True
    active = {mid: claim for mid, claim in selected.items() if claim.claim_id not in inactive}
    temporal_links = [link for link in chosen if link.relation != "CORRECTS"
                      and link.mention_id in active and link.target_mention_id in active]
    projected_mentions = tuple(replace(mentions[mid], readings=(selected_readings[mid],))
                               for mid in sorted(active))
    projected = replace(problem, mentions=projected_mentions, links=tuple(temporal_links))
    active_readings = {mid: reading_ids[mid] for mid in active}
    active_links = {mid: None for mid in active}
    for link in temporal_links:
        active_links[link.mention_id] = link.link_id
    active_claims = {(mid, reading_ids[mid]): claim for mid, claim in active.items()}
    memory = materialize_selection(projected, active_readings, active_links, active_claims)

    notices = []
    control_sources, inactive_controls = {}, {}
    for link in sorted(correction_links, key=lambda item: item.link_id):
        replacement, target = selected[link.mention_id], selected[link.target_mention_id]
        control = {replacement.source_id, target.source_id, *link.context_source_ids,
                   *replacement.context_source_ids, *target.context_source_ids,
                   *mentions[link.mention_id].context_source_ids,
                   *mentions[link.target_mention_id].context_source_ids,
                   *selected_readings[link.mention_id].context_source_ids,
                   *selected_readings[link.target_mention_id].context_source_ids,
                   *problem.context_source_ids}
        control_sources.setdefault(replacement.key, set()).update(control)
        inactive_controls.setdefault(target.claim_id, set()).update(control)
        notices.append({
            "link_id": link.link_id, "replacement_claim_id": replacement.claim_id,
            "target_claim_id": target.claim_id, "notice_source_id": replacement.source_id,
            "target_source_id": target.source_id,
            "authority_id": authorities[replacement.source_id].authority_id,
            "action": "replace", "coverage": "whole_assertion",
            "same_day_reference": source_map[replacement.source_id].available_at == source_map[target.source_id].available_at,
            "replacement_claim_active": replacement.claim_id not in inactive,
            "notice_effect_retained": True,
            "control_source_ids": sorted(control),
        })
    # A dependency can cross factual keys. Carry the withdrawal justification
    # through each inactive dependent before attaching it to same-key rivals;
    # otherwise a correction could remove a rival without appearing in the
    # resulting answer's provenance.
    for cid in inactive:
        if cid in dependencies:
            mid, claim = by_claim[cid]
            controls = inactive_controls.setdefault(cid, set())
            controls.update({
                claim.source_id, *claim.context_source_ids,
                *mentions[mid].context_source_ids,
                *selected_readings[mid].context_source_ids,
                *problem.context_source_ids,
            })
            for prerequisite in dependencies[cid] - set(by_claim):
                # Absence is conditional on the selected alternative for the
                # prerequisite's mention. That reading (including unresolved)
                # can use context that influenced the support-withdrawal decision.
                prerequisite_pair = next(pair for pair, candidate in claims.items()
                                         if candidate.claim_id == prerequisite)
                prerequisite_mid = prerequisite_pair[0]
                alternative = selected_readings[prerequisite_mid]
                controls.update({mentions[prerequisite_mid].source_id,
                                 *mentions[prerequisite_mid].context_source_ids,
                                 *alternative.context_source_ids})
                if not alternative.unresolved:
                    controls.update(claims[prerequisite_mid, alternative.reading_id].context_source_ids)
    changed = True
    while changed:
        changed = False
        for cid, prerequisites in dependencies.items():
            if cid not in inactive:
                continue
            controls = inactive_controls.setdefault(cid, set())
            before = len(controls)
            for prerequisite in prerequisites:
                controls.update(inactive_controls.get(prerequisite, ()))
            changed |= len(controls) != before
    for cid, controls in inactive_controls.items():
        control_sources.setdefault(by_claim[cid][1].key, set()).update(controls)
    # Preserve declared essential-support provenance for active derivatives.
    source_dependencies = {cid: set(ids) for cid, ids in memory.dependency_source_ids.items()}
    for mid, claim in active.items():
        source_dependencies[claim.claim_id].update(control_sources.get(claim.key, ()))
    changed = True
    while changed:
        changed = False
        for cid, prerequisites in dependencies.items():
            if cid not in source_dependencies:
                continue
            before = len(source_dependencies[cid])
            for prerequisite in prerequisites:
                source_dependencies[cid].update(source_dependencies[prerequisite])
            changed |= len(source_dependencies[cid]) != before
    ledger = {
        "policy_version": policy.version,
        "selected_claims": [claim_to_dict(selected[mid]) for mid in sorted(selected)],
        "selected_reading_ids": dict(sorted(reading_ids.items())),
        "selected_link_ids": dict(sorted(link_ids.items())),
        "active_claim_ids": sorted(claim.claim_id for claim in active.values()),
        "directly_withdrawn_claim_ids": sorted(directly_withdrawn),
        "inactive_claim_ids": sorted(inactive),
        "inactive_reasons": {cid: sorted(items) for cid, items in sorted(reasons.items())},
        "operative_notices": notices,
        "active_temporal_link_ids": sorted(link.link_id for link in temporal_links),
        "inactive_temporal_link_ids": sorted(link.link_id for link in chosen
                                              if link.relation != "CORRECTS" and link not in temporal_links),
        "control_source_ids": sorted({sid for ids in control_sources.values() for sid in ids}),
        "inactive_claim_control_source_ids": {cid: sorted(ids) for cid, ids in sorted(inactive_controls.items())},
        "support_dependency_semantics": "all_declared_prerequisites_are_essential",
        "restatement_dependency_semantics": "RESTATES_is_episode_identity_not_copying",
        "reinstatement_policy": "explicit_new_replacement_only; no_ancestor_resurrection",
        "certificate_status": "exact_spans_validated; authority_and_reference_entailment_not_certified",
        "time_policy": "no_event_boundary_added_from_correction_or_availability_date",
    }
    diagnostics = dict(memory.diagnostics)
    diagnostics.update({"correction_ledger": ledger,
                        "correction_materializer": "conditional_whole_assertion_replacement_v0.5",
                        "claim_dependency_source_ids": {cid: sorted(ids) for cid, ids in sorted(source_dependencies.items())}})
    return replace(memory, dependency_source_ids={cid: tuple(sorted(ids)) for cid, ids in source_dependencies.items()},
                   diagnostics=diagnostics)


def materialize_corrected_selection(problem: Problem, reading_ids: Mapping[str, str],
                                    link_ids: Mapping[str, str | None],
                                    claims: Mapping[tuple[str, str], TemporalClaim],
                                    policy: CorrectionPolicy) -> CoupledMemory:
    validate_correction_policy(problem, claims, policy)
    return _materialize(problem, reading_ids, link_ids, claims, policy, _prepare(problem, claims))


def make_correction_state_semantics(problem: Problem,
                                    claims: Mapping[tuple[str, str], TemporalClaim],
                                    policy: CorrectionPolicy):
    """Return a pure shared decoder hook replacing legacy global date checks."""
    frozen_claims = dict(claims)
    validate_correction_policy(problem, frozen_claims, policy)
    prepared = _prepare(problem, frozen_claims)
    ordered_mentions = sorted(problem.mentions, key=lambda mention: mention.mention_id)

    def state_semantics(selected, chosen):
        if set(selected) != set(prepared[0]):
            raise ValueError("Hook needs exactly one reading per mention")
        reading_ids = {mid: reading.reading_id for mid, reading in selected.items()}
        if any(prepared[1].get((mid, reading.reading_id)) != reading for mid, reading in selected.items()):
            raise ValueError("Hook selected reading differs from fixed candidate graph")
        if len(chosen) != len(ordered_mentions):
            raise ValueError("Hook needs one link or null per sorted mention")
        link_ids = {}
        for mention, link in zip(ordered_mentions, chosen):
            if link is not None and prepared[2].get(link.link_id) != link:
                raise ValueError("Hook selected link differs from fixed candidate graph")
            link_ids[mention.mention_id] = None if link is None else link.link_id
        try:
            memory = _materialize(problem, reading_ids, link_ids, frozen_claims, policy, prepared)
        except TemporalInfeasible:
            return None
        return tuple(tuple(group) for group in memory.diagnostics["restatement_components"])

    return state_semantics

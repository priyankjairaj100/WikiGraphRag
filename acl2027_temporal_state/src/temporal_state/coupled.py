"""Small exact temporal-network materializer conditional on a selected state.

This is standard difference-constraint closure, not a new decoding algorithm.
Only explicit RESTATES and CHANGES links couple intervals. Original claims,
modalities, and report horizons remain intact; no universal nonoverlap or
forward persistence is added.
"""
from dataclasses import dataclass, field
from datetime import date
from math import inf
from typing import Mapping

from .bounded import (
    IntervalEnvelope, TemporalClaim, build_bounded_memory, normalize_key,
)
from .decoder import Problem
from .models import Prediction, Question, Source


class TemporalInfeasible(ValueError):
    """A well-formed selected state has no supported temporal interpretation."""


@dataclass(frozen=True)
class CoupledMemory:
    sources: tuple[Source, ...]
    claims: tuple[TemporalClaim, ...]
    cutoff: str
    envelopes: Mapping[str, IntervalEnvelope]
    dependency_source_ids: Mapping[str, tuple[str, ...]]
    diagnostics: dict = field(default_factory=dict)

    def answer_key(self, question: Question, key: tuple[str, str, str],
                   query_mode: str = "announced_schedule") -> Prediction:
        """Apply bounded query semantics to exact conditional STN projections.

        An actual claim keeps its own original report horizon. Dates inferred
        through another episode never extend that horizon. All selected links
        have homogeneous modality, so planned constraints cannot tighten an
        actual episode. A later actual RESTATES claim can provide retrospective
        support under its own horizon, conditional on the selected same-episode
        relation; the earlier claim itself is not relabeled as later evidence.
        """
        if question.information_cutoff != self.cutoff:
            raise ValueError("Question cutoff must equal materialized source prefix")
        if query_mode not in {"reported_actual", "announced_schedule"}:
            raise ValueError("Unknown query mode")
        key = normalize_key(key)
        source_map = {source.source_id: source for source in self.sources}
        relevant, excluded_plans, excluded_projections, horizons = [], [], [], {}
        for claim in self.claims:
            if claim.key != key:
                continue
            if query_mode == "reported_actual" and claim.modality == "announced_future":
                excluded_plans.append(claim.claim_id)
                continue
            if query_mode == "reported_actual" and claim.modality == "reported_actual":
                horizon = claim.reported_at or source_map[claim.source_id].available_at
                horizons[claim.claim_id] = {
                    "date": horizon,
                    "basis": "original_reported_at" if claim.reported_at else "original_source_available_at_fallback",
                }
                if question.event_time > horizon:
                    excluded_projections.append(claim.claim_id)
                    continue
            membership = self.envelopes[claim.claim_id].membership(question.event_time)
            if membership.may:
                certain = membership.must and claim.modality not in {"conditional", "uncertain"}
                relevant.append((claim, certain))
        canonical = lambda value: " ".join(value.casefold().split())
        positives, negatives = {}, {}
        for claim, certain in relevant:
            table = positives if claim.polarity == "positive" else negatives
            table.setdefault(canonical(claim.value), []).append((claim, certain))
        certain_values = {value for value, records in positives.items() if any(certain for _, certain in records)}
        blocked = {value for value in positives if value not in certain_values
                   and any(certain for _, certain in negatives.get(value, []))}
        possible = set(positives) - blocked
        conflicts = certain_values & set(negatives)
        if len(certain_values) == 1 and possible == certain_values and not conflicts:
            value = next(iter(certain_values))
            supporters = [claim for claim, certain in positives[value] if certain]
            status, values = "answered", (min(supporters, key=lambda claim: claim.claim_id).value,)
            evidence = supporters + [claim for blocked_value in blocked
                                     for claim, certain in negatives.get(blocked_value, []) if certain]
        elif possible:
            status, values = "indeterminate", ()
            evidence = [claim for value in possible for claim, _ in positives[value]]
            evidence += [claim for value in possible for claim, _ in negatives.get(value, [])]
        else:
            status, values, evidence = "abstain", (), []
        primary = {claim.source_id for claim in evidence}
        dependencies = {sid for claim in evidence for sid in self.dependency_source_ids[claim.claim_id]}
        diagnostics = dict(self.diagnostics)
        diagnostics.update({
            "materializer": "conditional_temporal_network_v0.4",
            "query_mode": query_mode, "predicted_key": list(key),
            "possible_value_keys": sorted(possible), "certain_value_keys": sorted(certain_values),
            "blocked_possible_value_keys": sorted(blocked), "positive_negative_conflicts": sorted(conflicts),
            "excluded_plan_claim_ids": sorted(excluded_plans),
            "excluded_actual_projection_claim_ids": sorted(excluded_projections),
            "actual_horizons": horizons,
            "primary_evidence_source_ids": sorted(primary),
            "context_dependency_source_ids": sorted(dependencies - primary),
            "supporting_claim_modalities": {claim.claim_id: claim.modality for claim in evidence},
            "persistence_assumption": "connected_interval_only_within_explicit_selected_episode",
            "cross_claim_constraint_propagation": "selected_RESTATES_equal_endpoints_and_CHANGES_order_only",
            "single_valued_after_scope": True,
        })
        return Prediction(question.question_id, status, values,
                          tuple(sorted(primary | dependencies)),
                          tuple(sorted({claim.claim_id for claim in evidence})), diagnostics)


def _distinct(items, name):
    if len(items) != len(set(items)):
        raise ValueError(f"Duplicate {name}")


def _prepare(problem: Problem, claims: Mapping[tuple[str, str], TemporalClaim]):
    """Validate the complete candidate map before selecting any interpretation."""
    for name in ("sources", "mentions", "links", "context_source_ids", "penalties"):
        if not isinstance(getattr(problem, name), tuple):
            raise ValueError(f"Problem {name} must be an immutable tuple")
    _distinct([source.source_id for source in problem.sources], "source ID")
    sources = {source.source_id: source for source in problem.sources}
    if any(source.available_at > problem.cutoff for source in sources.values()):
        raise ValueError("All declared sources must be available in this prefix")

    def context(ids):
        if any(sid not in sources for sid in ids):
            raise ValueError("Every declared context source must be in the eligible prefix")

    context(problem.context_source_ids)
    _distinct([mention.mention_id for mention in problem.mentions], "mention ID")
    mentions = {mention.mention_id: mention for mention in problem.mentions}
    readings = {}
    resolved = set()
    for mention in problem.mentions:
        if not isinstance(mention.readings, tuple) or not isinstance(mention.context_source_ids, tuple):
            raise ValueError("Mention readings and context must be immutable tuples")
        if mention.source_id not in sources:
            raise ValueError("Unknown mention source")
        if (type(mention.span_start) is not int or type(mention.span_end) is not int
                or not 0 <= mention.span_start < mention.span_end <= len(sources[mention.source_id].text)):
            raise ValueError("Mention must have exact nonempty source offsets")
        context(mention.context_source_ids)
        _distinct([reading.reading_id for reading in mention.readings], "reading ID")
        for reading in mention.readings:
            if (not isinstance(reading.context_source_ids, tuple)
                    or not isinstance(reading.violations, tuple)
                    or (reading.key is not None and not isinstance(reading.key, tuple))):
                raise ValueError("Reading key, context, and violations must be immutable tuples")
            pair = mention.mention_id, reading.reading_id
            readings[pair] = reading
            context(reading.context_source_ids)
            if reading.unresolved:
                continue
            resolved.add(pair)
    if set(claims) != resolved:
        raise ValueError("Claim map must contain exactly every resolved candidate and no unresolved candidate")
    for pair, claim in claims.items():
        if not isinstance(claim, TemporalClaim):
            raise ValueError("Candidate claim must be a TemporalClaim")
        if any(not isinstance(getattr(claim, name), tuple)
               for name in ("state_observed_at", "evidence_spans", "context_source_ids")):
            raise ValueError("Claim observations, evidence, and context must be immutable tuples")
        mention, reading = mentions[pair[0]], readings[pair]
        if (claim.source_id != mention.source_id or claim.key != normalize_key(reading.key)
                or claim.value != reading.value):
            raise ValueError("Mapped claim must match candidate source, normalized key, and exact value")
        for exact, bounds in ((reading.effective_start, claim.start), (reading.effective_end, claim.end)):
            if exact is not None and (bounds.lower != exact or bounds.upper != exact):
                raise ValueError("Exact reading endpoint must match mapped claim's exact endpoint")
        if reading.observed_at is not None and reading.observed_at not in claim.state_observed_at:
            raise ValueError("Reading observation must be preserved in mapped claim")
    # This also checks claim-ID uniqueness, exact evidence, report dates, and
    # all declared claim context. Nothing is filtered from this strict prefix.
    build_bounded_memory(problem.sources, claims.values(), problem.cutoff)
    _distinct([link.link_id for link in problem.links], "link ID")
    links = {link.link_id: link for link in problem.links}
    for link in problem.links:
        if (not isinstance(link.context_source_ids, tuple) or not isinstance(link.violations, tuple)
                or (link.reference_span is not None and not isinstance(link.reference_span, tuple))):
            raise ValueError("Link context, violations, and reference span must be immutable tuples")
        if ((link.mention_id, link.reading_id) not in readings
                or (link.target_mention_id, link.target_reading_id) not in readings):
            raise ValueError("Link endpoint must name an existing candidate")
        context(link.context_source_ids)
        if link.relation not in {"RESTATES", "CHANGES", "CORRECTS"}:
            raise ValueError("Unknown link relation")
        if link.reference_span is not None:
            if (len(link.reference_span) != 2 or any(type(x) is not int for x in link.reference_span)
                    or not 0 <= link.reference_span[0] < link.reference_span[1]
                    <= len(sources[mentions[link.mention_id].source_id].text)):
                raise ValueError("Link reference span must be exact nonempty source offsets")
    return mentions, readings, links


def _solve(problem, reading_ids, link_ids, claims, prepared):
    mentions, readings, links = prepared
    if set(reading_ids) != set(mentions) or set(link_ids) != set(mentions):
        raise ValueError("Select exactly one reading and one link or null per mention")
    try:
        selected_readings = {mid: readings[mid, rid] for mid, rid in reading_ids.items()}
    except KeyError as exc:
        raise ValueError("Unknown selected reading") from exc
    selected = {mid: claims[mid, reading_ids[mid]] for mid, reading in selected_readings.items() if not reading.unresolved}
    chosen = []
    for mid, lid in link_ids.items():
        if lid is None:
            continue
        if lid not in links or links[lid].mention_id != mid:
            raise ValueError("Selected link must belong to its mention")
        link = links[lid]
        if (mid == link.target_mention_id or mid not in selected or link.target_mention_id not in selected
                or reading_ids[mid] != link.reading_id
                or reading_ids[link.target_mention_id] != link.target_reading_id):
            raise TemporalInfeasible("Link endpoints must be distinct selected resolved readings")
        left, right = selected[mid], selected[link.target_mention_id]
        if left.key != right.key:
            raise TemporalInfeasible("Selected link must preserve factual key")
        if link.relation == "CORRECTS":
            raise TemporalInfeasible("CORRECTS is unsupported by this temporal materializer")
        if left.modality != right.modality:
            raise TemporalInfeasible("Selected link cannot mix modalities")
        if link.relation == "RESTATES":
            if left.value != right.value or left.polarity != right.polarity:
                raise TemporalInfeasible("RESTATES requires equal value and polarity")
        elif left.value == right.value or left.polarity != "positive" or right.polarity != "positive":
            raise TemporalInfeasible("CHANGES requires positive claims with distinct values")
        chosen.append(link)

    parents = {mid: mid for mid in selected}

    def find(mid):
        while parents[mid] != mid:
            mid = parents[mid]
        return mid

    def union(a, b):
        a, b = find(a), find(b)
        parents[max(a, b)] = min(a, b)

    for link in chosen:
        if link.relation == "RESTATES":
            union(link.mention_id, link.target_mention_id)
    groups = {}
    for mid in sorted(selected):
        groups.setdefault(find(mid), []).append(mid)
    episodes = tuple(sorted(groups))
    variables = {episode: (2 * i + 1, 2 * i + 2) for i, episode in enumerate(episodes)}
    size = 1 + 2 * len(episodes)
    distance = [[0 if i == j else inf for j in range(size)] for i in range(size)]
    constraints = []

    def add(a, b, limit, kind, support):
        # x_b - x_a <= limit. Variable zero is the fixed reference origin.
        distance[a][b] = min(distance[a][b], limit)
        constraints.append({"from": a, "to": b, "upper": limit, "kind": kind, "support": support})

    def ordinal(value):
        return date.fromisoformat(value).toordinal()

    for episode, mids in groups.items():
        start, end = variables[episode]
        add(end, start, -1, "positive_integer_duration", list(mids))
        for mid in mids:
            claim = selected[mid]
            for var, bounds, label in ((start, claim.start, "start"), (end, claim.end, "end")):
                if bounds.lower is not None:
                    add(var, 0, -ordinal(bounds.lower), f"{label}_lower", claim.claim_id)
                if bounds.upper is not None:
                    add(0, var, ordinal(bounds.upper), f"{label}_upper", claim.claim_id)
            for observed in claim.state_observed_at:
                add(0, start, ordinal(observed), "observed_start_upper", claim.claim_id)
                add(end, 0, -ordinal(observed) - 1, "observed_end_lower", claim.claim_id)
    for link in chosen:
        if link.relation == "CHANGES":
            later_start = variables[find(link.mention_id)][0]
            earlier_end = variables[find(link.target_mention_id)][1]
            add(later_start, earlier_end, 0, "earlier_end_le_later_start", link.link_id)
    for k in range(size):
        for i in range(size):
            if distance[i][k] == inf:
                continue
            for j in range(size):
                distance[i][j] = min(distance[i][j], distance[i][k] + distance[k][j])
    if any(distance[i][i] < 0 for i in range(size)):
        raise TemporalInfeasible("Negative cycle: no integer-day temporal assignment satisfies selected links")
    envelopes = {}
    episode_envelopes = {}
    finite = lambda value: None if value in {inf, -inf} else int(value)
    for episode, mids in groups.items():
        start, end = variables[episode]
        envelope = IntervalEnvelope(finite(-distance[start][0]), finite(distance[0][start]),
                                    finite(-distance[end][0]), finite(distance[0][end]))
        episode_envelopes[episode] = envelope
        for mid in mids:
            envelopes[selected[mid].claim_id] = envelope

    # Connected-component provenance is deliberately conservative. Keep every
    # source that supplied a constraint, identity choice, or context to this
    # connected selection, even if its inequality was redundant after closure.
    for link in chosen:
        union(link.mention_id, link.target_mention_id)
    component_sources = {}
    for mid, claim in selected.items():
        deps = component_sources.setdefault(find(mid), set(problem.context_source_ids))
        deps.update((claim.source_id, *claim.context_source_ids,
                     *mentions[mid].context_source_ids, *selected_readings[mid].context_source_ids))
    for link in chosen:
        component_sources[find(link.mention_id)].update(link.context_source_ids)
    dependencies = {claim.claim_id: tuple(sorted(component_sources[find(mid)])) for mid, claim in selected.items()}
    diagnostics = {
        "selected_reading_ids": dict(sorted(reading_ids.items())),
        "selected_link_ids": dict(sorted(link_ids.items())),
        "restatement_components": [list(groups[group]) for group in episodes],
        "claim_episode_ids": {selected[mid].claim_id: episode for episode, mids in groups.items() for mid in mids},
        "claim_dependency_source_ids": {cid: list(ids) for cid, ids in sorted(dependencies.items())},
        "temporal_variable_count": size - 1,
        "temporal_constraints": constraints,
        "temporal_closure": "Floyd-Warshall exact integer difference constraints",
        "adjacency_assumption": False,
        "unlinked_nonoverlap_assumption": False,
        "actual_report_horizon_policy": "original_per_claim_horizon_unchanged",
        "claim_evidence_validation": "exact_source_slices; entailment_not_independently_certified",
        "certainty_scope": "conditional_on_selected_readings_links_and_connected_episode_assumption",
        "alternative_assignment_agreement_certified": False,
    }
    return CoupledMemory(problem.sources, tuple(selected[mid] for mid in sorted(selected)), problem.cutoff,
                         envelopes, dependencies, diagnostics)


def materialize_selection(problem: Problem, reading_ids: Mapping[str, str],
                          link_ids: Mapping[str, str | None],
                          claims: Mapping[tuple[str, str], TemporalClaim]) -> CoupledMemory:
    """Validate and materialize one selected state; contradictions raise.

    CHANGES points from the later mention to the earlier mention, matching the
    reference decoder. Null links add no temporal relation. CORRECTS is rejected
    only when selected, so candidate graphs may retain unsupported alternatives.
    """
    return _solve(problem, reading_ids, link_ids, claims, _prepare(problem, claims))


def selection_feasible(problem: Problem, reading_ids: Mapping[str, str],
                       link_ids: Mapping[str, str | None],
                       claims: Mapping[tuple[str, str], TemporalClaim]) -> bool:
    """Pure semantic feasibility predicate; malformed inputs still raise."""
    prepared = _prepare(problem, claims)
    try:
        _solve(problem, reading_ids, link_ids, claims, prepared)
    except TemporalInfeasible:
        return False
    return True


def make_feasibility_check(problem: Problem,
                           claims: Mapping[tuple[str, str], TemporalClaim]):
    """Validate once and return the decoder's optional (selected, chosen) hook.

    The hook does no scoring, file access, or mutation. Selected/choice identity
    is checked against the fixed problem. Candidate records are frozen copies of
    the supplied mapping, so later mapping mutations cannot alter search.
    """
    frozen_claims = dict(claims)
    prepared = _prepare(problem, frozen_claims)

    def check(selected, chosen):
        reading_ids = {mid: reading.reading_id for mid, reading in selected.items()}
        if any(prepared[1].get((mid, reading.reading_id)) != reading for mid, reading in selected.items()):
            raise ValueError("Hook selected reading differs from fixed candidate graph")
        ordered_mentions = sorted(problem.mentions, key=lambda mention: mention.mention_id)
        if len(chosen) != len(ordered_mentions):
            raise ValueError("Hook needs one link or null per sorted mention")
        link_ids = {}
        for mention, link in zip(ordered_mentions, chosen):
            if link is not None and prepared[2].get(link.link_id) != link:
                raise ValueError("Hook selected link differs from fixed candidate graph")
            link_ids[mention.mention_id] = None if link is None else link.link_id
        try:
            _solve(problem, reading_ids, link_ids, frozen_claims, prepared)
        except TemporalInfeasible:
            return False
        return True

    return check

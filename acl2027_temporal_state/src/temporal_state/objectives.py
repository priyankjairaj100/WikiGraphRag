"""Explicit shared objectives for correction-aware reference decoding.

The active-support variant is an ablation, not a calibrated truth likelihood.
Inactive evidence is neutral relative to existing unresolved/null alternatives;
it is not replaced with an arbitrary numeric zero. Feasibility remains the
responsibility of the shared semantic kernel, called before this scorer.
"""
from collections.abc import Mapping
import math

from .decoder import Link, Problem, Reading


MODES = ("historical", "active_support")


def objective_spec(mode="active_support"):
    """Return the complete question-independent, JSON-safe scoring convention."""
    if mode not in MODES:
        raise ValueError("Unknown objective mode")
    return {
        "version": "0.6",
        "mode": mode,
        "reading_net": "unary_score - sum(configured violation penalties)",
        "link_net": "beta * link_score - sum(configured violation penalties)",
        "unresolved_reading": "selected unresolved reading net score",
        "null_link": "mention null_score, without beta scaling",
        "inactive_resolved_reading": (
            "selected reading net score" if mode == "historical" else
            "maximum unresolved net score for this mention; reading-ID tie break"),
        "inactive_temporal_link": (
            "selected link net score" if mode == "historical" else
            "outgoing mention null_score"),
        "operative_correction_link": "selected link net score, including inactive replacement claims",
        "active_membership": "union of components from the shared feasible semantic projection",
        "active_temporal_link": "RESTATES/CHANGES with both selected endpoints active",
        "reference_reading_tie_break": "lexicographically smallest reading ID among maximum net unresolved scores",
        "independent_reading_policy": "unchanged per-mention maximum net unary; no global optimality claim",
        "scope": "objective ablation with shared candidates, scores, feasibility, and search budgets",
    }


def _finite(value, where):
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value)):
        raise ValueError(f"{where} must be finite numeric")
    return value


def _sum(values, where):
    try:
        value = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{where} must have a finite sum") from exc
    return _finite(value, where)


class _Objective:
    def __init__(self, problem, mode):
        self.spec = objective_spec(mode)
        self.mode = mode
        if not isinstance(problem, Problem):
            raise ValueError("Objective needs a Problem")
        self.beta = _finite(problem.beta, "beta")
        if self.beta < 0:
            raise ValueError("beta must be nonnegative")
        self.mentions = tuple(sorted(problem.mentions, key=lambda item: item.mention_id))
        self.by_mention = {item.mention_id: item for item in self.mentions}
        self.links = {item.link_id: item for item in problem.links}
        if len(self.by_mention) != len(self.mentions) or len(self.links) != len(problem.links):
            raise ValueError("Objective input IDs must be unique")
        self.weights = {item.name: _finite(item.weight, "penalty") for item in problem.penalties}
        if len(self.weights) != len(problem.penalties) or any(value < 0 for value in self.weights.values()):
            raise ValueError("Penalty names must be unique and weights nonnegative")
        self.readings, self.reading_net, self.references = {}, {}, {}
        for mention in self.mentions:
            _finite(mention.null_score, "null_score")
            for reading in mention.readings:
                pair = mention.mention_id, reading.reading_id
                if pair in self.readings:
                    raise ValueError("Reading IDs must be unique within a mention")
                self.readings[pair] = reading
                self.reading_net[pair] = self._net(reading.unary_score, reading.violations)
            unresolved = [reading for reading in mention.readings if reading.unresolved]
            if not unresolved:
                raise ValueError("Every mention requires an unresolved reference reading")
            reference = min(unresolved, key=lambda reading: (
                -self.reading_net[mention.mention_id, reading.reading_id], reading.reading_id))
            self.references[mention.mention_id] = (
                reference.reading_id, self.reading_net[mention.mention_id, reference.reading_id])
        self.link_net = {link.link_id: self._net(self.beta * _finite(link.score, "link score"),
                                                 link.violations)
                         for link in problem.links}

    def _net(self, score, violations):
        _finite(score, "score")
        if not isinstance(violations, tuple) or len(set(violations)) != len(violations):
            raise ValueError("Violations must be distinct immutable IDs")
        if any(name not in self.weights for name in violations):
            raise ValueError("Every violation requires a configured penalty")
        # Match the v0.5 decoder's arithmetic as well as its mathematical rule.
        return _finite(score - sum(self.weights[name] for name in violations), "net score")

    def _validate_selection(self, selected, chosen, components):
        if not isinstance(selected, Mapping) or set(selected) != set(self.by_mention):
            raise ValueError("Select exactly one reading per mention")
        for mid, reading in selected.items():
            if (not isinstance(reading, Reading)
                    or self.readings.get((mid, reading.reading_id)) != reading):
                raise ValueError("Selected reading must match the frozen problem")
        if not isinstance(chosen, tuple) or len(chosen) != len(self.mentions):
            raise ValueError("Chosen links must follow sorted mention order in a tuple")
        for mention, link in zip(self.mentions, chosen):
            if link is None:
                continue
            if (not isinstance(link, Link) or self.links.get(link.link_id) != link
                    or link.mention_id != mention.mention_id
                    or link.target_mention_id not in selected
                    or selected[link.mention_id].reading_id != link.reading_id
                    or selected[link.target_mention_id].reading_id != link.target_reading_id):
                raise ValueError("Chosen link must match its selected problem endpoints")
            if link.relation not in {"RESTATES", "CHANGES", "CORRECTS"}:
                raise ValueError("Unknown link relation")
        if (not isinstance(components, tuple)
                or any(not isinstance(group, tuple) or not group for group in components)):
            raise ValueError("Components must be immutable nonempty groups")
        members = [mid for group in components for mid in group]
        if (any(not isinstance(mid, str) or mid not in selected or selected[mid].unresolved
                for mid in members) or len(set(members)) != len(members)):
            raise ValueError("Components must contain distinct selected resolved mentions")
        if components != tuple(sorted(tuple(sorted(group)) for group in components)):
            raise ValueError("Components must have canonical ordering")
        return set(members)

    def breakdown(self, selected, chosen, components):
        active = self._validate_selection(selected, chosen, components)
        terms = {name: [] for name in (
            "active_claim_unary_net", "inactive_claim_unary_net", "inactive_claim_reference_net",
            "unresolved_unary_net", "active_temporal_link_net", "inactive_temporal_link_net",
            "inactive_temporal_reference_net", "correction_link_net", "null_link_score")}
        reading_rows, link_rows = [], []
        removed_reading_advantages, removed_link_advantages = [], []
        raw_reading_terms, raw_link_terms, used_reading_terms, used_link_terms = [], [], [], []
        for mention, link in zip(self.mentions, chosen):
            mid = mention.mention_id
            reading = selected[mid]
            raw = self.reading_net[mid, reading.reading_id]
            reference_id, reference = self.references[mid]
            is_inactive = not reading.unresolved and mid not in active
            used = reference if self.mode == "active_support" and is_inactive else raw
            category = ("unresolved_unary_net" if reading.unresolved else
                        "active_claim_unary_net" if not is_inactive else
                        "inactive_claim_reference_net" if self.mode == "active_support" else
                        "inactive_claim_unary_net")
            terms[category].append(used)
            raw_reading_terms.append(raw)
            used_reading_terms.append(used)
            if is_inactive:
                removed_reading_advantages.append(_sum([raw, -reference], "inactive unary advantage"))
            reading_rows.append({
                "mention_id": mid, "reading_id": reading.reading_id,
                "status": "unresolved" if reading.unresolved else "inactive" if is_inactive else "active",
                "selected_net": raw, "reference_reading_id": reference_id,
                "reference_net": reference, "scored_net": used,
            })
            if link is None:
                raw_link = used_link = mention.null_score
                link_category, status = "null_link_score", "null"
            else:
                raw_link = self.link_net[link.link_id]
                is_inactive_link = (link.relation != "CORRECTS"
                                    and not {link.mention_id, link.target_mention_id} <= active)
                used_link = (mention.null_score if self.mode == "active_support" and is_inactive_link
                             else raw_link)
                link_category = ("correction_link_net" if link.relation == "CORRECTS" else
                                 "inactive_temporal_reference_net" if is_inactive_link and self.mode == "active_support" else
                                 "inactive_temporal_link_net" if is_inactive_link else "active_temporal_link_net")
                status = "operative_correction" if link.relation == "CORRECTS" else "inactive_temporal" if is_inactive_link else "active_temporal"
                if is_inactive_link:
                    removed_link_advantages.append(_sum([raw_link, -mention.null_score], "inactive link advantage"))
            terms[link_category].append(used_link)
            raw_link_terms.append(raw_link)
            used_link_terms.append(used_link)
            link_rows.append({
                "mention_id": mid, "link_id": link.link_id if link else None,
                "status": status, "selected_net": raw_link,
                "reference_net": mention.null_score, "scored_net": used_link,
            })
        totals = {name: _sum(values, name) for name, values in terms.items()}
        total = _sum([*used_reading_terms, *used_link_terms], "objective")
        historical = _sum([*raw_reading_terms, *raw_link_terms], "historical objective")
        unary_mass = _sum(removed_reading_advantages, "inactive unary advantages")
        link_mass = _sum(removed_link_advantages, "inactive temporal advantages")
        return {
            "objective_spec": dict(self.spec), **totals,
            "total": total, "historical_total": historical,
            "historical_minus_selected_objective": _sum([historical, -total], "objective difference"),
            "inactive_unary_advantage_over_reference": unary_mass,
            "inactive_temporal_advantage_over_reference": link_mass,
            "removed_inactive_advantage": _sum([unary_mass, link_mass], "removed advantages") if self.mode == "active_support" else 0.0,
            "active_mention_ids": sorted(active),
            "reading_terms": reading_rows, "link_terms": link_rows,
            "interpretation": (
                "Historical-interpretation scores include withdrawn support." if self.mode == "historical" else
                "Inactive support contributes its existing unresolved/null reference; signed removed mass can be negative. This is not an answer-utility or truth objective."),
        }

    def __call__(self, selected, chosen, components):
        return self.breakdown(selected, chosen, components)["total"]


def make_objective_function(problem, mode="active_support"):
    """Build a deterministic callback for all shared decoder search paths.

    The kernel must call this only after semantic feasibility. It cannot infer
    active membership, correction admissibility, or source truth on its own.
    """
    return _Objective(problem, mode)


def objective_breakdown(problem, selected, chosen, components, mode="active_support"):
    """Audit every selected score and its reference under the named objective."""
    return _Objective(problem, mode).breakdown(selected, chosen, components)

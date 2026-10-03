"""Exact, deliberately small candidate-level reference decoder.

This module neither extracts candidates nor answers questions. All three methods
consume the same frozen, prefix-built problem and share the same objective and
feasibility function. Exhaustive enumeration is an implementation oracle for
authored diagnostics, not a scalable algorithm or an empirical quality result.
"""
from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json
import math
import random
from time import perf_counter

from .models import Source, validate_date


@dataclass(frozen=True)
class Reading:
    reading_id: str
    key: tuple[str, str, str] | None
    value: str | None
    unary_score: float
    effective_start: str | None = None
    effective_end: str | None = None
    observed_at: str | None = None
    context_source_ids: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()

    @property
    def unresolved(self):
        return self.key is None


@dataclass(frozen=True)
class Mention:
    mention_id: str
    source_id: str
    span_start: int
    span_end: int
    readings: tuple[Reading, ...]
    null_score: float = 0.0
    context_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Link:
    link_id: str
    mention_id: str
    reading_id: str
    target_mention_id: str
    target_reading_id: str
    relation: str
    score: float
    context_source_ids: tuple[str, ...] = ()
    # Offsets into the correcting source. Presence records extracted reference
    # evidence, not an independent entailment certification.
    reference_span: tuple[int, int] | None = None
    violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Penalty:
    name: str
    weight: float


@dataclass(frozen=True)
class Problem:
    cutoff: str
    sources: tuple[Source, ...]
    mentions: tuple[Mention, ...]
    links: tuple[Link, ...]
    score_provenance: str
    context_source_ids: tuple[str, ...] = ()
    penalties: tuple[Penalty, ...] = ()
    beta: float = 1.0


@dataclass(frozen=True)
class Limits:
    max_mentions: int = 8
    max_readings_per_mention: int = 5
    max_states: int = 250_000
    max_sweeps: int = 50
    max_restarts: int = 10


@dataclass(frozen=True)
class Diagnostics:
    prefix_digest: str
    state_ceiling: int
    reading_assignments_evaluated: int
    link_assignments_evaluated: int
    feasible_assignments_evaluated: int
    elapsed_seconds: float
    global_optimal: bool
    optimality_gap: float | None
    conditional_links_optimal: bool
    coordinate_converged: bool | None
    stop_reason: str
    restart_objectives: tuple[float, ...] = ()


@dataclass(frozen=True)
class Result:
    method: str
    objective: float
    reading_ids: tuple[tuple[str, str], ...]
    link_ids: tuple[tuple[str, str | None], ...]
    # Without state_semantics these are selected RESTATES components. A supplied
    # correction-aware kernel returns active components after support withdrawal.
    # The complete correction ledger belongs to the materializer, not this record.
    restatement_components: tuple[tuple[str, ...], ...]
    diagnostics: Diagnostics


class SearchLimitExceeded(ValueError):
    """The common conservative enumeration ceiling exceeds configured limits."""


def _tuple(value, name):
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an immutable tuple")


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _unique(items, name):
    if any(not isinstance(x, str) or not x for x in items) or len(items) != len(set(items)):
        raise ValueError(f"{name} must contain distinct nonempty string IDs")


def _span(start, end, text):
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text)):
        raise ValueError("Support spans must be nonempty code-point offsets into their source")


class _Engine:
    def __init__(self, problem, limits, feasibility_check=None, state_semantics=None,
                 objective_function=None):
        self.started = perf_counter()
        self.problem = problem
        self.limits = limits
        if feasibility_check is not None and not callable(feasibility_check):
            raise ValueError("feasibility_check must be callable or None")
        if state_semantics is not None and not callable(state_semantics):
            raise ValueError("state_semantics must be callable or None")
        if feasibility_check is not None and state_semantics is not None:
            raise ValueError("Choose one shared semantic extension, not both")
        # The optional evidence-only extension is shared by every search method.
        # It must be pure and deterministic; it may reject a semantically
        # inconsistent assignment, never inspect evaluation labels/questions.
        self.feasibility_check = feasibility_check
        self.state_semantics = state_semantics
        if objective_function is not None and not callable(objective_function):
            raise ValueError("objective_function must be callable or None")
        # A pure, question-free objective extension is common to every search.
        # It is evaluated only on states accepted by the shared semantics.
        self.objective_function = objective_function
        validate_date(problem.cutoff)
        for name in ("sources", "mentions", "links", "context_source_ids", "penalties"):
            _tuple(getattr(problem, name), name)
        for name in ("max_mentions", "max_readings_per_mention", "max_states", "max_sweeps"):
            if type(getattr(limits, name)) is not int or getattr(limits, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(limits.max_restarts) is not int or limits.max_restarts < 0:
            raise ValueError("max_restarts must be a nonnegative integer")
        _finite(problem.beta, "beta")
        if (problem.beta < 0 or not isinstance(problem.score_provenance, str)
                or not problem.score_provenance.strip()):
            raise ValueError("Nonnegative beta and explicit score provenance are required")
        _unique(tuple(s.source_id for s in problem.sources), "sources")
        self.sources = {s.source_id: s for s in problem.sources}
        # Deliberately reject full-corpus problems. Filtering an already generated
        # candidate graph cannot certify a prefix-built alias/context index.
        if any(s.available_at > problem.cutoff for s in problem.sources):
            raise ValueError("All declared sources must be available in this prefix")
        self._context(problem.context_source_ids)
        _unique(tuple(p.name for p in problem.penalties), "penalties")
        self.penalties = {p.name: p.weight for p in problem.penalties}
        for p in problem.penalties:
            _finite(p.weight, p.name)
            if p.weight < 0:
                raise ValueError("Penalty weights must be nonnegative")
        _unique(tuple(m.mention_id for m in problem.mentions), "mentions")
        self.mentions = tuple(sorted(problem.mentions, key=lambda m: m.mention_id))
        self.by_mention = {m.mention_id: m for m in self.mentions}
        self.readings = {}
        if len(self.mentions) > limits.max_mentions:
            raise SearchLimitExceeded("Mention count exceeds configured ceiling")
        for m in self.mentions:
            if m.source_id not in self.sources:
                raise ValueError("Unknown mention source")
            _span(m.span_start, m.span_end, self.sources[m.source_id].text)
            _finite(m.null_score, "null_score")
            self._context(m.context_source_ids)
            _tuple(m.readings, "readings")
            if not m.readings or len(m.readings) > limits.max_readings_per_mention:
                raise SearchLimitExceeded("Each mention needs 1..max_readings_per_mention readings")
            _unique(tuple(r.reading_id for r in m.readings), "readings")
            if not any(r.unresolved for r in m.readings):
                raise ValueError("Every mention requires an unresolved reading")
            for r in m.readings:
                self._validate_reading(r)
                self.readings[m.mention_id, r.reading_id] = r
        _unique(tuple(link.link_id for link in problem.links), "links")
        self.links = {link.link_id: link for link in problem.links}
        self.by_outgoing = {m.mention_id: [] for m in self.mentions}
        for link in sorted(problem.links, key=lambda link: link.link_id):
            if ((link.mention_id, link.reading_id) not in self.readings
                    or (link.target_mention_id, link.target_reading_id) not in self.readings):
                raise ValueError("Link endpoint must name an existing mention and reading")
            if link.relation not in {"RESTATES", "CHANGES", "CORRECTS"}:
                raise ValueError("Unknown link relation")
            _finite(link.score, "link score")
            self._context(link.context_source_ids)
            self._violation_cost(link.violations)
            if link.reference_span is not None:
                _tuple(link.reference_span, "reference_span")
                if len(link.reference_span) != 2:
                    raise ValueError("reference_span needs start/end offsets")
                source = self.sources[self.by_mention[link.mention_id].source_id]
                _span(*link.reference_span, source.text)
            self.by_outgoing[link.mention_id].append(link)
        self.state_ceiling = math.prod(len(m.readings) for m in self.mentions) * math.prod(
            1 + len(self.by_outgoing[m.mention_id]) for m in self.mentions)
        if self.state_ceiling > limits.max_states:
            raise SearchLimitExceeded(
                f"Conservative state ceiling {self.state_ceiling} exceeds {limits.max_states}")
        self.cache = {}
        self.link_evaluations = 0
        self.feasible_evaluations = 0
        # Sources, including normalized text, are hashed in stable ID order.
        # A production cache also needs the candidate/scorer/configuration digest.
        payload = [{"source_id": s.source_id, "text": s.text,
                    "available_at": s.available_at, "uri": s.uri,
                    "availability_basis": s.availability_basis, "provenance": s.provenance}
                   for s in sorted(problem.sources, key=lambda s: s.source_id)]
        self.prefix_digest = sha256(json.dumps(
            {"cutoff": problem.cutoff, "sources": payload}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _context(self, ids):
        _tuple(ids, "context_source_ids")
        _unique(ids, "context_source_ids")
        if any(sid not in self.sources for sid in ids):
            raise ValueError("Every declared context source must belong to the eligible prefix")

    def _violation_cost(self, violations):
        _tuple(violations, "violations")
        _unique(violations, "violations")
        if any(name not in self.penalties for name in violations):
            raise ValueError("Every violation must have an explicitly configured penalty")
        return sum(self.penalties[name] for name in violations)

    def _validate_reading(self, r):
        _finite(r.unary_score, "unary_score")
        self._context(r.context_source_ids)
        self._violation_cost(r.violations)
        for d in (r.effective_start, r.effective_end, r.observed_at):
            if d is not None:
                validate_date(d)
        if r.unresolved:
            if any(x is not None for x in (r.value, r.effective_start, r.effective_end, r.observed_at)):
                raise ValueError("Unresolved readings must not invent a value or exact date")
            return
        _tuple(r.key, "key")
        if (len(r.key) != 3 or any(not isinstance(x, str) for x in r.key)
                or not r.key[0].strip() or not r.key[1].strip()
                or not isinstance(r.value, str) or not r.value.strip()):
            raise ValueError("Resolved readings require a (subject, relation, scope) key and value")
        if r.effective_start and r.effective_end and r.effective_start >= r.effective_end:
            raise ValueError("Exact episode intervals must have positive duration")
        if r.observed_at and ((r.effective_start and r.observed_at < r.effective_start)
                              or (r.effective_end and r.observed_at >= r.effective_end)):
            raise ValueError("Observation must fall inside its stated exact interval")

    def selected(self, reading_ids):
        if set(reading_ids) != set(self.by_mention):
            raise ValueError("Select exactly one reading per mention")
        try:
            return {mid: self.readings[mid, rid] for mid, rid in reading_ids.items()}
        except KeyError as exc:
            raise ValueError("Unknown selected reading") from exc

    def local_link_ok(self, link, selected):
        a = selected[link.mention_id]
        b = selected[link.target_mention_id]
        if (a.reading_id != link.reading_id or b.reading_id != link.target_reading_id
                or link.mention_id == link.target_mention_id or a.unresolved or b.unresolved
                or a.key != b.key):
            return False
        if link.relation == "RESTATES":
            return a.value == b.value
        if link.relation == "CHANGES":
            return a.value != b.value
        source = self.sources[self.by_mention[link.mention_id].source_id]
        target = self.sources[self.by_mention[link.target_mention_id].source_id]
        return (target.available_at < source.available_at
                or (target.available_at == source.available_at and link.reference_span is not None))

    def feasible(self, selected, chosen):
        if any(not self.local_link_ok(link, selected) for link in chosen if link is not None):
            return None
        if self.state_semantics is not None:
            # Corrections can remove constraints, so support projection must
            # precede ALL cross-claim temporal checks. This kernel replaces the
            # legacy global check; it must check cycles and active constraints.
            components = self.state_semantics(selected, tuple(chosen))
            if components is None:
                return None
            if (not isinstance(components, tuple)
                    or any(not isinstance(group, tuple) or not group for group in components)):
                raise ValueError("state_semantics must return immutable components or None")
            members = [mid for group in components for mid in group]
            if (any(not isinstance(mid, str) or mid not in selected or selected[mid].unresolved
                    for mid in members) or len(set(members)) != len(members)):
                raise ValueError("Semantic components must contain distinct selected resolved mentions")
            if components != tuple(sorted(tuple(sorted(group)) for group in components)):
                raise ValueError("Semantic components must have canonical ordering")
            return components
        parents = {mid: mid for mid in selected}

        def find(mid):
            while parents[mid] != mid:
                mid = parents[mid]
            return mid

        for link in chosen:
            if link is not None and link.relation == "RESTATES":
                a, b = find(link.mention_id), find(link.target_mention_id)
                parents[max(a, b)] = min(a, b)
        groups = {}
        for mid, r in selected.items():
            if not r.unresolved:
                groups.setdefault(find(mid), []).append(mid)
        dates = {}
        for group, mids in groups.items():
            starts = {selected[mid].effective_start for mid in mids} - {None}
            ends = {selected[mid].effective_end for mid in mids} - {None}
            observations = sorted({selected[mid].observed_at for mid in mids} - {None})
            if len(starts) > 1 or len(ends) > 1:
                return None
            start, end = next(iter(starts), None), next(iter(ends), None)
            if start and end and start >= end:
                return None
            if observations and ((start and observations[0] < start)
                                 or (end and observations[-1] >= end)):
                return None
            dates[group] = (start, end, observations)
        changes = {group: set() for group in groups}
        corrections = {mid: set() for mid in selected}
        for link in chosen:
            if link is not None and link.relation == "CHANGES":
                changes[find(link.mention_id)].add(find(link.target_mention_id))
            elif link is not None and link.relation == "CORRECTS":
                corrections[link.mention_id].add(link.target_mention_id)
        ancestors = _transitive_ancestors(changes)
        if ancestors is None or _transitive_ancestors(corrections) is None:
            return None
        for later, earlier_groups in ancestors.items():
            for earlier in earlier_groups:
                if not _dates_ordered(dates[later], dates[earlier]):
                    return None
        if self.feasibility_check is not None:
            accepted = self.feasibility_check(selected, tuple(chosen))
            if type(accepted) is not bool:
                raise ValueError("feasibility_check must return a bool")
            if not accepted:
                return None
        return tuple(sorted(tuple(sorted(mids)) for mids in groups.values()))

    def objective(self, selected, chosen, components):
        if self.objective_function is not None:
            value = self.objective_function(selected, tuple(chosen), components)
            _finite(value, "custom assignment objective")
            return float(value)
        terms = [r.unary_score - self._violation_cost(r.violations) for r in selected.values()]
        for m, link in zip(self.mentions, chosen):
            terms.append(m.null_score if link is None else (
                self.problem.beta * link.score - self._violation_cost(link.violations)))
        for term in terms:
            _finite(term, "weighted objective term")
        value = math.fsum(terms)
        _finite(value, "objective")
        return value

    def optimize_links(self, ids):
        key = tuple(ids[m.mention_id] for m in self.mentions)
        if key in self.cache:
            return self.cache[key]
        selected = self.selected(ids)
        choices = [(None,) + tuple(link for link in self.by_outgoing[m.mention_id]
                                   if self.local_link_ok(link, selected)) for m in self.mentions]
        best = None
        for chosen in product(*choices):
            self.link_evaluations += 1
            components = self.feasible(selected, chosen)
            if components is None:
                continue
            self.feasible_evaluations += 1
            state = (self.objective(selected, chosen, components), tuple(sorted(ids.items())),
                     tuple((m.mention_id, link.link_id if link else None)
                           for m, link in zip(self.mentions, chosen)), components)
            if _better(state, best):
                best = state
        # Null for every mention always supplies a feasible interpretation.
        if best is None:
            raise ValueError("No feasible link assignment; validate the selected candidate contract")
        self.cache[key] = best
        return best

    def independent_ids(self):
        return {m.mention_id: min(m.readings, key=lambda r: (
                    -(r.unary_score - self._violation_cost(r.violations)), r.reading_id)).reading_id
                for m in self.mentions}

    def result(self, method, state, converged=None, stop_reason="exhausted", restarts=()):
        return Result(method, *state, Diagnostics(
            self.prefix_digest, self.state_ceiling, len(self.cache), self.link_evaluations,
            self.feasible_evaluations, perf_counter() - self.started, method == "joint_exact",
            0.0 if method == "joint_exact" else None, True, converged, stop_reason, restarts))


def _transitive_ancestors(graph):
    """Return all predecessor sets, or None for a directed cycle."""
    done, visiting = {}, set()

    def visit(node):
        if node in visiting:
            return None
        if node in done:
            return done[node]
        visiting.add(node)
        found = set()
        for parent in graph[node]:
            parents = visit(parent)
            if parents is None:
                return None
            found.update(parents)
            found.add(parent)
        visiting.remove(node)
        done[node] = found
        return found

    for node in graph:
        if visit(node) is None:
            return None
    return done


def _dates_ordered(later, earlier):
    """Exact boundaries/observations constrain order; unknowns stay unknown."""
    ls, le, lo = later
    es, ee, eo = earlier
    if ((ls and es and ls <= es) or (ls and ee and ls < ee)
            or (le and es and le <= es) or (le and ee and le <= ee)):
        return False
    if ((eo and ls and eo[-1] >= ls) or (eo and le and eo[-1] >= le)
            or (lo and ee and lo[0] < ee) or (lo and es and lo[0] <= es)
            or (lo and eo and eo[-1] >= lo[0])):
        return False
    return True


def _tie_key(state):
    return (state[1], tuple((mid, link or "") for mid, link in state[2]))


def _better(state, best):
    return best is None or state[0] > best[0] or (state[0] == best[0] and _tie_key(state) < _tie_key(best))


def evaluate_assignment(problem: Problem, reading_ids: dict[str, str],
                        link_ids: dict[str, str | None], limits: Limits = Limits(), *,
                        feasibility_check=None, state_semantics=None,
                        objective_function=None) -> float | None:
    """Shared objective; None means semantically infeasible, malformed IDs raise."""
    engine = _Engine(problem, limits, feasibility_check, state_semantics, objective_function)
    selected = engine.selected(reading_ids)
    if set(link_ids) != set(engine.by_mention):
        raise ValueError("Select one link or null per mention")
    chosen = []
    for m in engine.mentions:
        lid = link_ids[m.mention_id]
        if lid is None:
            chosen.append(None)
        elif lid not in engine.links or engine.links[lid].mention_id != m.mention_id:
            raise ValueError("Selected link must belong to its mention")
        else:
            chosen.append(engine.links[lid])
    components = engine.feasible(selected, chosen)
    return None if components is None else engine.objective(selected, chosen, components)


def decode_independent(problem: Problem, limits: Limits = Limits(), *,
                       feasibility_check=None, state_semantics=None,
                       objective_function=None) -> Result:
    """Freeze local readings including local penalties; optimize shared links.

    Ignoring an available unary violation penalty would weaken this baseline
    even in a graph with no interactions and create a spurious joint gain.
    """
    engine = _Engine(problem, limits, feasibility_check, state_semantics, objective_function)
    return engine.result("independent", engine.optimize_links(engine.independent_ids()))


def decode_joint(problem: Problem, limits: Limits = Limits(), *,
                 feasibility_check=None, state_semantics=None,
                 objective_function=None) -> Result:
    """Exhaust every reading and feasible primary-link assignment within limits."""
    engine = _Engine(problem, limits, feasibility_check, state_semantics, objective_function)
    best = None
    for readings in product(*(sorted(m.readings, key=lambda r: r.reading_id) for m in engine.mentions)):
        ids = {m.mention_id: r.reading_id for m, r in zip(engine.mentions, readings)}
        state = engine.optimize_links(ids)
        if _better(state, best):
            best = state
    return engine.result("joint_exact", best)


def decode_iterative(problem: Problem, limits: Limits = Limits(), *, restarts: int = 0,
                     seed: int = 0, initial_reading_ids: dict[str, str] | None = None,
                     feasibility_check=None, state_semantics=None,
                     objective_function=None) -> Result:
    """Coordinate ascent with exact link reoptimization after each reading trial.

    Start from unary choices (or an explicit diagnostic initialization). Optional
    random restarts sample the identical candidate set; ties never move a state.
    This is stronger than a repair step which holds selected links fixed.
    """
    engine = _Engine(problem, limits, feasibility_check, state_semantics, objective_function)
    if type(restarts) is not int or not 0 <= restarts <= limits.max_restarts:
        raise ValueError("Restart count must be within the configured ceiling")
    start = engine.independent_ids() if initial_reading_ids is None else dict(initial_reading_ids)
    engine.selected(start)
    rng = random.Random(seed)
    starts = [start]
    for _ in range(restarts):
        starts.append({m.mention_id: rng.choice(sorted(m.readings, key=lambda r: r.reading_id)).reading_id
                       for m in engine.mentions})
    best, all_converged, objectives = None, True, []
    for ids in starts:
        current = engine.optimize_links(ids)
        converged = False
        for _ in range(limits.max_sweeps):
            changed = False
            for m in engine.mentions:
                candidate_best = current
                for r in sorted(m.readings, key=lambda r: r.reading_id):
                    trial = dict(current[1])
                    trial[m.mention_id] = r.reading_id
                    state = engine.optimize_links(trial)
                    if _better(state, candidate_best):
                        candidate_best = state
                if candidate_best[0] > current[0]:
                    current = candidate_best
                    changed = True
            if not changed:
                converged = True
                break
        objectives.append(current[0])
        all_converged = all_converged and converged
        if _better(current, best):
            best = current
    return engine.result("iterative", best, all_converged,
                         "coordinate_fixed_point" if all_converged else "sweep_limit",
                         tuple(objectives))

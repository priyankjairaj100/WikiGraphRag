"""Supplied-judgment witness-selection scaffold, NOT a natural-data retriever.

Hypotheses, incompatibilities, source judgments and unknown-coverage assessment
are externally supplied (possibly oracle). No proposer, verifier or tokenizer is
implemented here. Selection cannot certify the truth of these inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from hashlib import sha256
from itertools import combinations
from typing import Callable


class Judgment(str, Enum):
    SUPPORTED = 'supported'
    CONTRADICTED = 'contradicted'
    UNRESOLVED = 'unresolved'


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


@dataclass(frozen=True)
class Source:
    """One immutable supplied source object (e.g. a physical page), exact text."""
    source_id: str
    text: str
    text_sha256: str
    locator: str

    def __post_init__(self):
        require(bool(self.source_id and self.locator), 'Source identity/locator required')
        require(sha256(self.text.encode()).hexdigest() == self.text_sha256,
                'Source text hash mismatch')


@dataclass(frozen=True, order=True)
class Span:
    """Half-open Unicode offsets in an immutable Source; not token offsets."""
    source_id: str
    start: int
    stop: int

    def __post_init__(self):
        require(type(self.start) is int and type(self.stop) is int and
                0 <= self.start < self.stop, 'Invalid nonempty source span')


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    required_claims: tuple[str, ...]
    weight: Fraction = Fraction(1)

    def __post_init__(self):
        require(bool(self.hypothesis_id), 'Hypothesis ID required')
        require(bool(self.required_claims) and all(self.required_claims) and
                len(set(self.required_claims)) == len(self.required_claims),
                'Nonempty distinct required claims prevent vacuous support')
        require(isinstance(self.weight, Fraction) and self.weight >= 0,
                'Use a fixed nonnegative Fraction weight')


@dataclass(frozen=True)
class Bundle:
    """All spans are selected together; no partial bundle packing exists."""
    bundle_id: str
    spans: tuple[Span, ...]


@dataclass(frozen=True)
class SourceJudgment:
    """Supplied separator/support map activated only by ALL required spans.

    Activation depends on received source coverage, never the bundle's name.
    A positive or contradictory rule must have actual nonempty source anchors;
    this checks presence, not whether the supplied semantic judgment is true.
    """
    judgment_id: str
    hypothesis_id: str
    claim_id: str
    verdict: Judgment
    required_spans: tuple[Span, ...]
    provenance: str


@dataclass(frozen=True)
class Problem:
    sources: tuple[Source, ...]
    hypotheses: tuple[Hypothesis, ...]
    bundles: tuple[Bundle, ...]
    judgments: tuple[SourceJudgment, ...]
    incompatible_pairs: frozenset[tuple[str, str]]
    input_provenance: str
    unknown_alternatives_remain: bool = True
    unknown_assessment_provenance: str = ''
    prompt_prefix: str = ''

    def __post_init__(self):
        require(bool(self.input_provenance), 'Disclose supplied/oracle input provenance')
        require(bool(self.hypotheses), 'A nonempty hypothesis set is required')
        for objects, field in ((self.sources, 'source_id'),
                               (self.hypotheses, 'hypothesis_id'),
                               (self.bundles, 'bundle_id'),
                               (self.judgments, 'judgment_id')):
            ids = [getattr(item, field) for item in objects]
            require(all(ids) and len(ids) == len(set(ids)), 'Empty or duplicate IDs')
        sources = {s.source_id: s for s in self.sources}
        hypotheses = {h.hypothesis_id: h for h in self.hypotheses}
        for bundle in self.bundles:
            require(bool(bundle.spans), 'Indivisible bundle must contain source spans')
        for span in [s for b in self.bundles for s in b.spans] + [
                s for j in self.judgments for s in j.required_spans]:
            require(isinstance(span, Span) and span.source_id in sources and
                    span.stop <= len(sources[span.source_id].text), 'Unknown/out-of-range span')
        for judgment in self.judgments:
            require(judgment.hypothesis_id in hypotheses, 'Unknown judgment hypothesis')
            require(judgment.claim_id in hypotheses[judgment.hypothesis_id].required_claims,
                    'Unknown judgment claim')
            require(isinstance(judgment.verdict, Judgment), 'Use a three-valued Judgment')
            require(bool(judgment.required_spans and judgment.provenance),
                    'Judgment requires anchored source and supplied provenance')
        for pair in self.incompatible_pairs:
            require(len(pair) == 2 and pair[0] < pair[1] and
                    all(h in hypotheses for h in pair),
                    'Pairs must be distinct, canonical, known hypothesis IDs')
        require(type(self.unknown_alternatives_remain) is bool, 'Unknown flag must be boolean')
        require(self.unknown_alternatives_remain or bool(self.unknown_assessment_provenance),
                'Clearing unknown alternatives requires external assessment provenance')


@dataclass(frozen=True)
class Cost:
    amount: int
    unit: str
    provenance: str

    def __post_init__(self):
        require(type(self.amount) is int and self.amount >= 0, 'Nonnegative integer cost required')
        require(bool(self.unit and self.provenance), 'Cost unit/counter provenance required')


Counter = Callable[[str], Cost]


@dataclass(frozen=True)
class Evaluation:
    selected_bundle_ids: tuple[str, ...]
    received_spans: tuple[Span, ...]
    cost: Cost
    activated_judgment_ids: tuple[str, ...]
    claim_states: tuple[tuple[str, str, Judgment], ...]
    survivors: tuple[str, ...]
    supported_hypotheses: tuple[str, ...]
    incompatible_survivor_pairs: tuple[tuple[str, str], ...]
    conflicts: tuple[tuple[str, str], ...]
    loss: Fraction
    admissible: bool
    answerable: bool
    blockers: tuple[str, ...]


def union_spans(spans: tuple[Span, ...]) -> tuple[Span, ...]:
    """Deduplicate overlaps/adjacency, preserve source identities and real gaps."""
    merged: list[Span] = []
    for span in sorted(set(spans)):
        if merged and merged[-1].source_id == span.source_id and span.start <= merged[-1].stop:
            previous = merged.pop()
            merged.append(Span(span.source_id, previous.start, max(previous.stop, span.stop)))
        else:
            merged.append(span)
    return tuple(merged)


def render(problem: Problem, spans: tuple[Span, ...]) -> str:
    """Canonical union text INCLUDING source labels and caller prompt prefix.

    Counter must apply the actual reader template if claiming native tokens.
    Character/word fixture counters remain fixtures, not native token estimates.
    """
    sources = {source.source_id: source for source in problem.sources}
    pieces = [problem.prompt_prefix] if problem.prompt_prefix else []
    for span in union_spans(spans):
        source = sources[span.source_id]
        pieces.append(f'[{source.source_id} | {source.locator} | chars={span.start}:{span.stop}'
                      f' | sha256={source.text_sha256}]\n{source.text[span.start:span.stop]}')
    return '\n\n'.join(pieces)


def covers(received: tuple[Span, ...], required: Span) -> bool:
    return any(s.source_id == required.source_id and s.start <= required.start and
               s.stop >= required.stop for s in received)


def evaluate(problem: Problem, selected: tuple[str, ...], counter: Counter,
             budget: int, cost_unit: str) -> Evaluation:
    require(type(budget) is int and budget >= 0, 'Invalid budget')
    require(len(selected) == len(set(selected)), 'Duplicate selected bundle IDs')
    bundles = {bundle.bundle_id: bundle for bundle in problem.bundles}
    require(all(b in bundles for b in selected), 'Unknown selected bundle')
    spans = union_spans(tuple(s for b in selected for s in bundles[b].spans))
    cost = counter(render(problem, spans))
    require(isinstance(cost, Cost) and cost.unit == cost_unit, 'Counter/cost-unit mismatch')
    active = tuple(j for j in problem.judgments if all(covers(spans, s) for s in j.required_spans))
    states = []
    conflicts = []
    survivors = []
    supported = []
    for hypothesis in sorted(problem.hypotheses, key=lambda h: h.hypothesis_id):
        local = []
        for claim in hypothesis.required_claims:
            values = {j.verdict for j in active if j.hypothesis_id == hypothesis.hypothesis_id
                      and j.claim_id == claim}
            if Judgment.SUPPORTED in values and Judgment.CONTRADICTED in values:
                # A contested contradiction cannot silently erase an alternative.
                state = Judgment.UNRESOLVED
                conflicts.append((hypothesis.hypothesis_id, claim))
            elif Judgment.CONTRADICTED in values:
                state = Judgment.CONTRADICTED
            elif Judgment.SUPPORTED in values:
                state = Judgment.SUPPORTED
            else:
                state = Judgment.UNRESOLVED
            states.append((hypothesis.hypothesis_id, claim, state))
            local.append(state)
        if Judgment.CONTRADICTED not in local:
            survivors.append(hypothesis.hypothesis_id)
        if all(state is Judgment.SUPPORTED for state in local):
            supported.append(hypothesis.hypothesis_id)
    pairs = tuple(sorted(p for p in problem.incompatible_pairs if all(h in survivors for h in p)))
    weights = {h.hypothesis_id: h.weight for h in problem.hypotheses}
    # Fixed weights, never survivor-normalized. Zero-weight alternatives remain
    # logically incompatible and can block answers even when loss is zero.
    loss = sum((weights[a] * weights[b] for a, b in pairs), Fraction(0))
    blockers = []
    if cost.amount > budget:
        blockers.append('over_budget')
    if not survivors:
        blockers.append('no_surviving_hypothesis')
    if conflicts:
        blockers.append('conflicting_source_judgments')
    if pairs:
        blockers.append('incompatible_survivors')
    if not supported:
        blockers.append('missing_positive_support')
    if problem.unknown_alternatives_remain:
        blockers.append('unmodeled_alternatives_unresolved')
    admissible = cost.amount <= budget and bool(survivors) and not conflicts
    return Evaluation(tuple(sorted(selected)), spans, cost,
                      tuple(sorted(j.judgment_id for j in active)), tuple(states),
                      tuple(survivors), tuple(supported), pairs, tuple(conflicts), loss,
                      admissible, not blockers, tuple(blockers))


def _supported_claim_count(state: Evaluation) -> int:
    return sum(h in state.survivors and value is Judgment.SUPPORTED
               for h, _, value in state.claim_states)


def greedy_select(problem: Problem, counter: Counter, budget: int, cost_unit: str) -> Evaluation:
    """Myopic loss-gain/cost heuristic, with positive-support tie breaking.

    Cannot discover all complementary witnesses. No approximation guarantee.
    A result may legitimately abstain; inspect answerable and blockers.
    """
    state = evaluate(problem, (), counter, budget, cost_unit)
    if not state.admissible:
        return state
    remaining = sorted(b.bundle_id for b in problem.bundles)
    while remaining:
        choices = []
        for bid in remaining:
            candidate = evaluate(problem, state.selected_bundle_ids + (bid,), counter, budget, cost_unit)
            if not candidate.admissible:
                continue
            gain = state.loss - candidate.loss
            support_gain = _supported_claim_count(candidate) - _supported_claim_count(state)
            if gain <= 0 and support_gain <= 0 and not (candidate.answerable and not state.answerable):
                continue
            # Labels can disappear when intervals merge; exact full cost can
            # have zero/negative marginal change. This denominator is heuristic.
            divisor = max(1, candidate.cost.amount - state.cost.amount)
            score = (gain / divisor, candidate.answerable,
                     Fraction(support_gain, divisor), -candidate.cost.amount)
            choices.append((score, bid, candidate))
        if not choices:
            break
        best_score = max(item[0] for item in choices)
        _, bid, state = min((item for item in choices if item[0] == best_score), key=lambda item: item[1])
        remaining.remove(bid)
    return state


def exhaustive_tiny_optimum(problem: Problem, counter: Counter, budget: int,
                            cost_unit: str, *, require_answerable: bool = False,
                            max_bundles: int = 16) -> Evaluation | None:
    """Debug-only finite subset optimum on the SUPPLIED maps, not real truth.

    Objective order: pair loss, answerable tie-break, exact cost, bundle count,
    lexical IDs. By default returns an admissible selection even if abstention
    is necessary; require_answerable=True restricts the feasible set explicitly.
    """
    require(type(max_bundles) is int and 0 <= max_bundles <= 16,
            'Exhaustive debug cap must be between 0 and 16 bundles')
    require(len(problem.bundles) <= max_bundles, 'Too many bundles for tiny exhaustive debug search')
    ids = tuple(sorted(b.bundle_id for b in problem.bundles))
    best = None
    best_key = None
    for size in range(len(ids) + 1):
        for chosen in combinations(ids, size):
            state = evaluate(problem, chosen, counter, budget, cost_unit)
            if not state.admissible or (require_answerable and not state.answerable):
                continue
            key = (state.loss, not state.answerable, state.cost.amount, size, chosen)
            if best_key is None or key < best_key:
                best_key, best = key, state
    return best

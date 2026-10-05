"""Exact finite certificates over a SUPPLIED witness map, version 21.

This module authenticates neither sources nor semantics. Atom IDs are a caller's
prevalidated representation of immutable source coverage; source intervals must
be split at every action and premise endpoint before calling it. In particular,
an atom must not hide an uncovered gap or merge distinct document versions.

The complete signed witness map is known to this offline diagnostic solver.
Consequently its optimum is not the cost of a realizable online retrieval policy.
CoverageAssertion records an external assertion, never proves completeness.
"""

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from typing import Callable, Dict, Optional, Tuple, Union


Rational = Union[int, Fraction]
MAX_EXACT_ACTIONS = 20


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _ids(value, name, *, nonempty=False):
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be a tuple")
    for item in value:
        _text(item, name)
    if len(value) != len(set(value)):
        raise ValueError(f"{name} contains duplicate IDs")
    if nonempty and not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _rational(value, name):
    # Floating-point values, NaN, infinity, and bool are deliberately rejected.
    if isinstance(value, bool) or not isinstance(value, (int, Fraction)):
        raise ValueError(f"{name} must be an integer or Fraction")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return Fraction(value)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    action_class: str
    required_claims: Tuple[str, ...]

    def __post_init__(self):
        _text(self.candidate_id, "candidate_id")
        _text(self.action_class, "action_class")
        _ids(self.required_claims, "required_claims", nonempty=True)


@dataclass(frozen=True)
class Action:
    action_id: str
    atoms: Tuple[str, ...]

    def __post_init__(self):
        _text(self.action_id, "action_id")
        _ids(self.atoms, "action atoms", nonempty=True)


@dataclass(frozen=True)
class Witness:
    witness_id: str
    candidate_id: str
    claim_id: str
    sign: str  # '+' or '-'; absence of either is unresolved, not negative.
    premise_atoms: Tuple[str, ...]
    provenance: str

    def __post_init__(self):
        for name in ("witness_id", "candidate_id", "claim_id", "provenance"):
            _text(getattr(self, name), name)
        if self.sign not in ("+", "-"):
            raise ValueError("sign must be '+' or '-'")
        _ids(self.premise_atoms, "premise_atoms", nonempty=True)


@dataclass(frozen=True)
class CoverageAssertion:
    cleared: bool
    provenance: str

    def __post_init__(self):
        if not isinstance(self.cleared, bool):
            raise ValueError("coverage cleared must be bool")
        _text(self.provenance, "coverage provenance")


@dataclass(frozen=True)
class Problem:
    atoms: Tuple[str, ...]
    candidates: Tuple[Candidate, ...]
    actions: Tuple[Action, ...]
    witnesses: Tuple[Witness, ...]
    coverage: CoverageAssertion
    atom_provenance: str
    input_provenance: str
    initial_atoms: Tuple[str, ...] = ()

    def __post_init__(self):
        _ids(self.atoms, "atoms")
        _ids(self.initial_atoms, "initial_atoms")
        _text(self.atom_provenance, "atom_provenance")
        _text(self.input_provenance, "input_provenance")
        if not isinstance(self.coverage, CoverageAssertion):
            raise ValueError("coverage must be CoverageAssertion")
        for field, cls, key in (("candidates", Candidate, "candidate_id"),
                                ("actions", Action, "action_id"),
                                ("witnesses", Witness, "witness_id")):
            rows = getattr(self, field)
            if not isinstance(rows, tuple) or any(not isinstance(x, cls) for x in rows):
                raise ValueError(f"{field} must be a tuple of {cls.__name__}")
            _ids(tuple(getattr(x, key) for x in rows), field, nonempty=field == "candidates")
        atom_set = set(self.atoms)
        if not set(self.initial_atoms) <= atom_set:
            raise ValueError("initial_atoms contain undeclared atoms")
        candidates = {x.candidate_id: x for x in self.candidates}
        for action in self.actions:
            if not set(action.atoms) <= atom_set:
                raise ValueError(f"action {action.action_id} contains undeclared atoms")
        for witness in self.witnesses:
            candidate = candidates.get(witness.candidate_id)
            if candidate is None or witness.claim_id not in candidate.required_claims:
                raise ValueError(f"witness {witness.witness_id} has an unknown candidate/claim")
            if not set(witness.premise_atoms) <= atom_set:
                raise ValueError(f"witness {witness.witness_id} contains undeclared atoms")


@dataclass(frozen=True)
class Cost:
    amount: Fraction
    unit: str
    provenance: str

    def __post_init__(self):
        object.__setattr__(self, "amount", _rational(self.amount, "cost amount"))
        _text(self.unit, "cost unit")
        _text(self.provenance, "cost provenance")


# Both arguments are sorted, deduplicated tuples. Initial atoms are in received.
# Passing selected actions permits genuine per-action acquisition charges too.
CostFunction = Callable[[Tuple[str, ...], Tuple[str, ...]], Cost]


@dataclass(frozen=True)
class Evaluation:
    selected_action_ids: Tuple[str, ...]
    received_atoms: Tuple[str, ...]
    activated_witness_ids: Tuple[str, ...]
    claim_states: Tuple[Tuple[str, str, str], ...]
    conflicts: Tuple[Tuple[str, str], ...]
    survivors: Tuple[str, ...]
    supported_candidates: Tuple[str, ...]
    certificate_candidates: Tuple[str, ...]
    cost: Cost
    within_budget: bool
    local_certificate: bool
    coverage_cleared: bool
    authorized: bool
    blockers: Tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    status: str  # OPTIMAL or INFEASIBLE, for the declared finite objective only.
    objective: str  # authorized_certificate or local_certificate
    best: Optional[Evaluation]
    evaluated_subsets: int
    action_count: int
    coverage_cleared: bool
    scope: str = "offline finite supplied-map optimum; no online-policy guarantee"


class SearchLimitError(ValueError):
    """Refusal to search; supplies no feasibility or optimality conclusion."""


class _Compiled:
    def __init__(self, problem):
        if not isinstance(problem, Problem):
            raise ValueError("problem must be Problem")
        self.problem = problem
        self.atoms = tuple(sorted(problem.atoms))
        positions = {atom: 1 << i for i, atom in enumerate(self.atoms)}
        self.mask = lambda atoms: sum(positions[a] for a in atoms)
        self.initial = self.mask(problem.initial_atoms)
        self.actions = {a.action_id: self.mask(a.atoms) for a in problem.actions}
        self.witnesses = tuple((w, self.mask(w.premise_atoms))
                               for w in sorted(problem.witnesses, key=lambda x: x.witness_id))
        self.candidates = tuple(sorted(problem.candidates, key=lambda x: x.candidate_id))

    def evaluate(self, selected, counter, budget, budget_unit):
        _ids(selected, "selected_action_ids")
        if not set(selected) <= self.actions.keys():
            raise ValueError("selection contains unknown action IDs")
        selected = tuple(sorted(selected))
        received_mask = self.initial
        for action_id in selected:
            received_mask |= self.actions[action_id]
        received = tuple(a for i, a in enumerate(self.atoms) if received_mask & (1 << i))
        signs = {(h.candidate_id, c): set() for h in self.candidates for c in h.required_claims}
        active = []
        for witness, premise in self.witnesses:
            if received_mask & premise == premise:
                active.append(witness.witness_id)
                signs[(witness.candidate_id, witness.claim_id)].add(witness.sign)
        conflicts = tuple(sorted(key for key, value in signs.items() if len(value) == 2))
        states = tuple((h, c, "supported" if s == {"+"} else
                        "contradicted" if s == {"-"} else "unresolved")
                       for (h, c), s in sorted(signs.items()))
        # A conflict is unresolved for claim display and survival diagnostics.
        # Only an uncontested negative refutes a candidate; global conflict
        # still blocks every certificate, including on unchosen candidates.
        survivors = tuple(h.candidate_id for h in self.candidates
                          if all(signs[h.candidate_id, c] != {"-"} for c in h.required_claims))
        supported = tuple(h.candidate_id for h in self.candidates
                          if all(signs[h.candidate_id, c] == {"+"} for c in h.required_claims))
        certificates = () if conflicts else tuple(
            h.candidate_id for h in self.candidates if h.candidate_id in supported
            and all(g.action_class == h.action_class or
                    any("-" in signs[g.candidate_id, c] for c in g.required_claims)
                    for g in self.candidates))
        cost = counter(selected, received)
        if not isinstance(cost, Cost):
            raise ValueError("cost function must return Cost")
        if budget is not None and cost.unit != budget_unit:
            raise ValueError("cost unit does not match budget_unit")
        within = budget is None or cost.amount <= budget
        local = bool(certificates)
        blockers = []
        if conflicts:
            blockers.append("CONFLICT")
        if not survivors:
            blockers.append("NO_SURVIVOR")
        if not supported:
            blockers.append("NO_COMPLETE_POSITIVE_SUPPORT")
        if len({h.action_class for h in self.candidates if h.candidate_id in survivors}) > 1:
            blockers.append("UNREFUTED_RIVAL_ACTION")
        if not self.problem.coverage.cleared:
            blockers.append("COVERAGE_UNRESOLVED")
        if not within:
            blockers.append("BUDGET_EXCEEDED")
        return Evaluation(selected, received, tuple(active), states, conflicts, survivors,
                          supported, certificates, cost, within, local,
                          self.problem.coverage.cleared,
                          local and within and self.problem.coverage.cleared,
                          tuple(blockers))


def _budget(budget, budget_unit):
    if budget is None:
        if budget_unit is not None:
            raise ValueError("budget_unit requires a budget")
        return None
    _text(budget_unit, "budget_unit")
    return _rational(budget, "budget")


def evaluate(problem: Problem, selected_action_ids: Tuple[str, ...],
             cost_fn: CostFunction, *, budget: Optional[Rational] = None,
             budget_unit: Optional[str] = None) -> Evaluation:
    """Evaluate known judgments, not their truth; no acquisition is performed."""
    bound = _budget(budget, budget_unit)
    if not callable(cost_fn):
        raise ValueError("cost_fn must be callable")
    return _Compiled(problem).evaluate(selected_action_ids, cost_fn, bound, budget_unit)


def solve_exact(problem: Problem, cost_fn: CostFunction, *,
                budget: Optional[Rational] = None, budget_unit: Optional[str] = None,
                require_coverage: bool = True,
                max_actions: int = MAX_EXACT_ACTIONS) -> SearchResult:
    """Enumerate all action subsets; arbitrary nonnegative costs may decrease.

    No cost or feasibility pruning is performed. The callback must be pure and
    deterministic over this fixed problem and return a consistent cost unit and
    provenance. Its correctness and determinism are caller assumptions.
    Ties use cost, action count, then lexicographic selected IDs. More actions
    than the declared cap raise SearchLimitError, never INFEASIBLE. Setting
    require_coverage=False computes only a local finite certificate optimum;
    inspect best.authorized separately. Every result is offline and conditional
    on the supplied map, atom normalization, and declared cost semantics.
    """
    bound = _budget(budget, budget_unit)
    if not isinstance(require_coverage, bool):
        raise ValueError("require_coverage must be bool")
    if isinstance(max_actions, bool) or not isinstance(max_actions, int) or not 0 <= max_actions <= MAX_EXACT_ACTIONS:
        raise ValueError(f"max_actions must be an integer from 0 to {MAX_EXACT_ACTIONS}")
    if not callable(cost_fn):
        raise ValueError("cost_fn must be callable")
    compiled = _Compiled(problem)
    action_ids = tuple(sorted(compiled.actions))
    if len(action_ids) > max_actions:
        raise SearchLimitError(f"{len(action_ids)} actions exceed cap {max_actions}; search not run")
    best = None
    best_key = None
    ledger = None
    evaluated = 0
    for size in range(len(action_ids) + 1):
        for selected in combinations(action_ids, size):
            state = compiled.evaluate(selected, cost_fn, bound, budget_unit)
            evaluated += 1
            identity = (state.cost.unit, state.cost.provenance)
            if ledger is None:
                ledger = identity
            elif identity != ledger:
                raise ValueError("cost function changed unit or provenance during search")
            eligible = state.local_certificate and state.within_budget
            if require_coverage:
                eligible = eligible and state.coverage_cleared
            if eligible:
                key = (state.cost.amount, len(selected), selected)
                if best_key is None or key < best_key:
                    best, best_key = state, key
    return SearchResult("OPTIMAL" if best is not None else "INFEASIBLE",
                        "authorized_certificate" if require_coverage else "local_certificate",
                        best, evaluated, len(action_ids), problem.coverage.cleared)


def additive_cost(atom_costs: Dict[str, Rational], *, unit: str, provenance: str,
                  action_costs: Optional[Dict[str, Rational]] = None,
                  fixed_cost: Rational = 0) -> CostFunction:
    """Explicit nonnegative additive ledger, not a native-token approximation.

    Every received atom must have a cost entry, including initial atoms. When
    action_costs is supplied every selected action must have an entry too.
    Shared atoms are charged once. Source access already incurred but not
    represented by received atoms must be charged separately by the caller.
    """
    _text(unit, "unit")
    _text(provenance, "provenance")
    ac = {_text(k, "atom cost ID"): _rational(v, "atom cost") for k, v in atom_costs.items()}
    xc = None if action_costs is None else {
        _text(k, "action cost ID"): _rational(v, "action cost") for k, v in action_costs.items()}
    base = _rational(fixed_cost, "fixed_cost")

    def counter(selected, received):
        try:
            total = base + sum((ac[a] for a in received), Fraction(0))
            if xc is not None:
                total += sum((xc[a] for a in selected), Fraction(0))
        except KeyError as exc:
            raise ValueError(f"missing additive cost for {exc.args[0]}") from exc
        return Cost(total, unit, provenance)

    return counter


def problem_from_dict(data: dict) -> Problem:
    """Strict JSON-shaped input; rejects unrecognized fields and lossy coercions."""
    def keys(row, required, optional=()):
        if not isinstance(row, dict) or set(row) - set(required) - set(optional) or set(required) - set(row):
            raise ValueError(f"expected fields {sorted(required)}, optional {sorted(optional)}")

    def sequence(value, label):
        if not isinstance(value, list):
            raise ValueError(f"{label} must be a JSON list")
        return tuple(value)

    keys(data, ("atoms", "candidates", "actions", "witnesses", "coverage",
                "atom_provenance", "input_provenance"), ("initial_atoms",))
    candidates, actions, witnesses = [], [], []
    for row in sequence(data["candidates"], "candidates"):
        keys(row, ("candidate_id", "action_class", "required_claims"))
        candidates.append(Candidate(row["candidate_id"], row["action_class"],
                                    sequence(row["required_claims"], "required_claims")))
    for row in sequence(data["actions"], "actions"):
        keys(row, ("action_id", "atoms"))
        actions.append(Action(row["action_id"], sequence(row["atoms"], "action atoms")))
    for row in sequence(data["witnesses"], "witnesses"):
        keys(row, ("witness_id", "candidate_id", "claim_id", "sign", "premise_atoms", "provenance"))
        witnesses.append(Witness(row["witness_id"], row["candidate_id"], row["claim_id"], row["sign"],
                                  sequence(row["premise_atoms"], "premise_atoms"), row["provenance"]))
    keys(data["coverage"], ("cleared", "provenance"))
    return Problem(sequence(data["atoms"], "atoms"), tuple(candidates), tuple(actions), tuple(witnesses),
                   CoverageAssertion(**data["coverage"]), data["atom_provenance"], data["input_provenance"],
                   sequence(data.get("initial_atoms", []), "initial_atoms"))

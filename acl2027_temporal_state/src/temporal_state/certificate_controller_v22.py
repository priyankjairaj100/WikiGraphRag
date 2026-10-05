"""Non-anticipating acquisition against a v21 certificate.

The planner may rank actions. It cannot write a sign into the ledger.
The verifier is called only for obligations whose premise atoms have already
been acquired and charged. An unresolved judgment adds nothing. An observed
conflict ends the run. An open coverage assertion cannot authorize an answer.

The action pack that first passes the gate is the pack this planner happened
to acquire. It is not a minimum-cost certificate. Use certificate_v21.solve_exact
for that offline diagnostic. This module does not read sources or establish
that a judgment is true.
"""

from dataclasses import dataclass, replace
from typing import Callable, Optional, Tuple

from temporal_state.certificate_v21 import (
    Cost, CoverageAssertion, Evaluation, Problem, Witness, evaluate,
)


class ControllerError(ValueError):
    """The caller broke the acquisition contract. No run result is produced."""


Planner = Callable[["VisibleState"], Tuple[str, ...]]
Verifier = Callable[["VisibleState", Tuple["Obligation", ...]], Tuple["Judgment", ...]]
CoverageGate = Callable[["VisibleState"], CoverageAssertion]

AUTHORIZED = "AUTHORIZED"
CONFLICT = "CONFLICT"
COVERAGE_UNRESOLVED = "COVERAGE_UNRESOLVED"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
EXHAUSTED_ACTIONS = "EXHAUSTED_ACTIONS"
PLANNER_STOP = "PLANNER_STOP"
STEP_LIMIT = "STEP_LIMIT"
SCOPE = (
    "online charged acquisition; first observed gate outcome; "
    "no optimality or semantic guarantee"
)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ControllerError(f"{name} must be a nonempty string")
    return value


@dataclass(frozen=True)
class Obligation:
    """Unsigned prospective witness. A sign is not part of this object."""

    obligation_id: str
    candidate_id: str
    claim_id: str
    premise_atoms: Tuple[str, ...]

    def __post_init__(self):
        _text(self.obligation_id, "obligation_id")
        _text(self.candidate_id, "candidate_id")
        _text(self.claim_id, "claim_id")
        if not isinstance(self.premise_atoms, tuple) or not self.premise_atoms:
            raise ControllerError("premise_atoms must be a nonempty tuple")
        if len(self.premise_atoms) != len(set(self.premise_atoms)):
            raise ControllerError("premise_atoms contains duplicates")
        for atom in self.premise_atoms:
            _text(atom, "premise atom")


@dataclass(frozen=True)
class Judgment:
    obligation_id: str
    sign: str  # '+', '-', or 'U'. Only the first two enter the ledger.

    def __post_init__(self):
        _text(self.obligation_id, "obligation_id")
        if self.sign not in ("+", "-", "U"):
            raise ControllerError("judgment sign must be '+', '-' or 'U'")


@dataclass(frozen=True)
class ActionView:
    action_id: str
    atoms: Tuple[str, ...]
    acquired: bool
    unaffordable: bool


@dataclass(frozen=True)
class VisibleState:
    """What selection may use. Atom ids are not source text."""

    received_atoms: Tuple[str, ...]
    selected_action_ids: Tuple[str, ...]
    observed_witnesses: Tuple[Witness, ...]
    unresolved_obligation_ids: Tuple[str, ...]
    open_obligation_ids: Tuple[str, ...]
    actions: Tuple[ActionView, ...]
    blockers: Tuple[str, ...]
    local_certificate: bool
    authorized: bool
    coverage_cleared: bool
    coverage_provenance: str
    spent: Cost
    step_index: int


@dataclass(frozen=True)
class StepRecord:
    index: int
    pre_received_atoms: Tuple[str, ...]
    pre_selected_action_ids: Tuple[str, ...]
    pre_blockers: Tuple[str, ...]
    pre_authorized: bool
    proposed_action_ids: Tuple[str, ...]
    acquired_action_id: Optional[str]
    verifier_request_ids: Tuple[str, ...]
    new_witness_ids: Tuple[str, ...]
    post_blockers: Tuple[str, ...]
    post_authorized: bool
    post_cost_amount: str


@dataclass(frozen=True)
class RunResult:
    stop_reason: str
    authorized: bool
    acquisition_order: Tuple[str, ...]
    steps: Tuple[StepRecord, ...]
    evaluation: Evaluation
    observed_witness_ids: Tuple[str, ...]
    initial_verifier_request_ids: Tuple[str, ...]
    scope: str = SCOPE


def _obligations(problem, obligations):
    if not isinstance(obligations, tuple):
        raise ControllerError("obligations must be a tuple")
    claims = {h.candidate_id: set(h.required_claims) for h in problem.candidates}
    atoms = set(problem.atoms)
    seen = set()
    for item in obligations:
        if not isinstance(item, Obligation):
            raise ControllerError("obligations must contain Obligation values")
        if item.obligation_id in seen:
            raise ControllerError(f"duplicate obligation {item.obligation_id}")
        seen.add(item.obligation_id)
        if item.claim_id not in claims.get(item.candidate_id, ()):
            raise ControllerError(f"obligation {item.obligation_id} has an unknown candidate/claim")
        if not set(item.premise_atoms) <= atoms:
            raise ControllerError(f"obligation {item.obligation_id} cites an undeclared atom")
    return obligations


def _choice(proposal, known, selected, unaffordable):
    if not isinstance(proposal, tuple):
        raise ControllerError("planner must return a tuple of action ids")
    if any(not isinstance(item, str) for item in proposal):
        raise ControllerError("planner action ids must be strings")
    if len(proposal) != len(set(proposal)):
        raise ControllerError("planner repeated an action id")
    unknown = [item for item in proposal if item not in known]
    if unknown:
        raise ControllerError(f"planner proposed unknown action {unknown[0]}")
    for action_id in proposal:
        if action_id not in selected and action_id not in unaffordable:
            return action_id
    return None


class _Run:
    def __init__(self, problem, obligations, verifier, cost_fn, budget, budget_unit, coverage_fn):
        if not isinstance(problem, Problem):
            raise ControllerError("problem must be a certificate Problem")
        if problem.witnesses:
            raise ControllerError("initial witnesses are not accepted; acquire premises first")
        if not callable(verifier) or not callable(cost_fn):
            raise ControllerError("verifier and cost_fn must be callable")
        if coverage_fn is not None and not callable(coverage_fn):
            raise ControllerError("coverage_fn must be callable")
        self.base = problem
        self.obligations = _obligations(problem, obligations)
        self.verifier = verifier
        self.cost_fn = cost_fn
        self.budget = budget
        self.budget_unit = budget_unit
        self.coverage_fn = coverage_fn
        self.coverage = problem.coverage
        self.selected = []
        self.witnesses = []
        self.judged = {}
        self.unaffordable = set()
        self.known = {action.action_id: action for action in problem.actions}
        self.steps = []
        self.initial_requests = ()

    def problem(self):
        return replace(self.base, witnesses=tuple(self.witnesses), coverage=self.coverage)

    def evaluation(self):
        return evaluate(self.problem(), tuple(self.selected), self.cost_fn,
                        budget=self.budget, budget_unit=self.budget_unit)

    def visible(self, evaluation):
        unresolved = tuple(sorted(key for key, sign in self.judged.items() if sign == "U"))
        open_ids = tuple(item.obligation_id for item in self.obligations if item.obligation_id not in self.judged)
        views = tuple(ActionView(action.action_id, action.atoms, action.action_id in self.selected,
                                  action.action_id in self.unaffordable)
                      for action in self.base.actions)
        return VisibleState(evaluation.received_atoms, evaluation.selected_action_ids,
                            tuple(self.witnesses), unresolved, open_ids, views, evaluation.blockers,
                            evaluation.local_certificate, evaluation.authorized, evaluation.coverage_cleared,
                            self.coverage.provenance, evaluation.cost, len(self.steps))

    def pending(self, received):
        have = set(received)
        ready = [item for item in self.obligations
                 if item.obligation_id not in self.judged and set(item.premise_atoms) <= have]
        return tuple(sorted(ready, key=lambda item: item.obligation_id))

    def apply(self, visible, pending):
        if not pending:
            return ()
        response = self.verifier(visible, pending)
        if not isinstance(response, tuple) or any(not isinstance(item, Judgment) for item in response):
            raise ControllerError("verifier must return a tuple of Judgment values")
        expected = {item.obligation_id for item in pending}
        got = tuple(item.obligation_id for item in response)
        if len(got) != len(set(got)) or set(got) != expected:
            raise ControllerError("verifier must answer exactly the pending obligations")
        by_id = {item.obligation_id: item for item in pending}
        new_ids = []
        for judgment in response:
            if judgment.sign == "U":
                self.judged[judgment.obligation_id] = "U"
                continue
            obligation = by_id[judgment.obligation_id]
            witness = Witness(obligation.obligation_id, obligation.candidate_id, obligation.claim_id,
                              judgment.sign, obligation.premise_atoms,
                              f"observed after acquisition; obligation {obligation.obligation_id}")
            self.witnesses.append(witness)
            self.judged[obligation.obligation_id] = judgment.sign
            new_ids.append(witness.witness_id)
        return tuple(new_ids)

    def refresh_coverage(self, visible):
        if self.coverage_fn is None:
            return
        gate = self.coverage_fn(visible)
        if not isinstance(gate, CoverageAssertion):
            raise ControllerError("coverage_fn must return CoverageAssertion")
        self.coverage = gate

    def finish(self, reason, evaluation):
        return RunResult(reason, evaluation.authorized, tuple(self.selected), tuple(self.steps),
                         evaluation, tuple(item.witness_id for item in self.witnesses),
                         self.initial_requests)

    def terminal(self, evaluation):
        if evaluation.authorized:
            return AUTHORIZED
        if "CONFLICT" in evaluation.blockers:
            return CONFLICT
        if evaluation.local_certificate and not evaluation.coverage_cleared:
            return COVERAGE_UNRESOLVED
        return None

    def record(self, pre, proposal, acquired, requests, new_ids, post):
        self.steps.append(StepRecord(
            len(self.steps), pre.received_atoms, pre.selected_action_ids, pre.blockers,
            pre.authorized, proposal, acquired, requests, new_ids, post.blockers,
            post.authorized, format(post.spent.amount, "f")))


def run_acquisition(problem: Problem, obligations: Tuple[Obligation, ...], planner: Planner,
                    verifier: Verifier, cost_fn, *, budget=None, budget_unit=None,
                    max_steps: int = 32, coverage_fn: Optional[CoverageGate] = None) -> RunResult:
    """Acquire until the observed gate stops the run. Predictions are not judgments."""
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 0:
        raise ControllerError("max_steps must be a nonnegative integer")
    if not callable(planner):
        raise ControllerError("planner must be callable")
    run = _Run(problem, obligations, verifier, cost_fn, budget, budget_unit, coverage_fn)
    opened = run.evaluation()
    run.refresh_coverage(run.visible(opened))
    opened = run.evaluation()
    prelude = run.visible(opened)
    run.initial_requests = tuple(item.obligation_id for item in run.pending(opened.received_atoms))
    run.apply(prelude, run.pending(opened.received_atoms))
    while True:
        current = run.evaluation()
        run.refresh_coverage(run.visible(current))
        current = run.evaluation()
        reason = run.terminal(current)
        if reason is not None:
            return run.finish(reason, current)
        unacquired = [action_id for action_id in run.known if action_id not in run.selected]
        if not unacquired:
            return run.finish(EXHAUSTED_ACTIONS, current)
        if all(action_id in run.unaffordable for action_id in unacquired):
            return run.finish(BUDGET_EXCEEDED, current)
        if len(run.steps) >= max_steps:
            return run.finish(STEP_LIMIT, current)
        pre = run.visible(current)
        proposal = planner(pre)
        choice = _choice(proposal, run.known, set(run.selected), run.unaffordable)
        if choice is None:
            # No remaining affordable action is handled above. Reaching here means
            # the planner declined to name one, which is not a budget result.
            run.record(pre, proposal, None, (), (), pre)
            return run.finish(PLANNER_STOP, current)
        trial = evaluate(run.problem(), tuple([*run.selected, choice]), run.cost_fn,
                         budget=run.budget, budget_unit=run.budget_unit)
        if not trial.within_budget:
            run.unaffordable.add(choice)
            post = run.visible(run.evaluation())
            run.record(pre, proposal, None, (), (), post)
            continue
        run.selected.append(choice)
        acquired = run.evaluation()
        view = run.visible(acquired)
        pending = run.pending(acquired.received_atoms)
        new_ids = run.apply(view, pending)
        post = run.visible(run.evaluation())
        run.record(pre, proposal, choice, tuple(item.obligation_id for item in pending), new_ids, post)

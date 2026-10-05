"""Invented acquisition traces. These are not filings, model calls, or QA results."""

import unittest

from temporal_state.certificate_controller_v22 import (
    AUTHORIZED, BUDGET_EXCEEDED, CONFLICT, COVERAGE_UNRESOLVED, EXHAUSTED_ACTIONS,
    PLANNER_STOP, ControllerError, Judgment, Obligation, VisibleState, run_acquisition,
)
from temporal_state.certificate_v21 import (
    Action, Candidate, Cost, CoverageAssertion, Problem, Witness,
)


UNIT = "authored_cost_units"
PROVENANCE = "Invented unit cost, not tokens."


def problem(*, cleared=True, initial=(), witnesses=()):
    return Problem(
        ("header", "note", "row"),
        (Candidate("a", "segment", ("value", "binding")),
         Candidate("b", "consolidated", ("value",))),
        (Action("row", ("row",)), Action("header", ("header",)), Action("note", ("note",))),
        witnesses, CoverageAssertion(cleared, "Invented coverage flag."),
        "Invented atoms.", "Invented controller problem.", tuple(initial))


OBLIGATIONS = (
    Obligation("a-value", "a", "value", ("row",)),
    Obligation("a-binding", "a", "binding", ("row", "header")),
    Obligation("not-b", "b", "value", ("note",)),
)
SIGNS = {"a-value": "+", "a-binding": "+", "not-b": "-"}


def cost(selected, received):
    return Cost(len(selected), UNIT, PROVENANCE)


def planner(visible):
    assert isinstance(visible, VisibleState)
    return ("row", "header", "note")


def honest(visible, pending):
    return tuple(Judgment(item.obligation_id, SIGNS[item.obligation_id]) for item in pending)


def unresolved(visible, pending):
    return tuple(Judgment(item.obligation_id, "U") for item in pending)


class AcquisitionContract(unittest.TestCase):
    def test_unresolved_judgments_never_enter_the_ledger(self):
        requested = []

        def verifier(visible, pending):
            requested.append(tuple(item.obligation_id for item in pending))
            return unresolved(visible, pending)

        result = run_acquisition(problem(), OBLIGATIONS, planner, verifier, cost)
        self.assertEqual(result.stop_reason, EXHAUSTED_ACTIONS)
        self.assertFalse(result.authorized)
        self.assertEqual(result.observed_witness_ids, ())
        self.assertEqual(result.acquisition_order, ("row", "header", "note"))
        self.assertEqual(requested, [("a-value",), ("a-binding",), ("not-b",)])
        self.assertEqual(result.steps[0].pre_received_atoms, ())
        self.assertEqual(result.steps[0].verifier_request_ids, ("a-value",))
        self.assertNotIn("a-binding", result.steps[0].verifier_request_ids)

    def test_open_coverage_cannot_authorize_a_local_certificate(self):
        result = run_acquisition(problem(cleared=False), OBLIGATIONS, planner, honest, cost)
        self.assertEqual(result.stop_reason, COVERAGE_UNRESOLVED)
        self.assertTrue(result.evaluation.local_certificate)
        self.assertFalse(result.authorized)
        self.assertEqual(result.evaluation.certificate_candidates, ("a",))

    def test_observed_support_and_rival_refutation_authorize(self):
        result = run_acquisition(problem(), OBLIGATIONS, planner, honest, cost)
        self.assertEqual(result.stop_reason, AUTHORIZED)
        self.assertTrue(result.authorized)
        self.assertEqual(result.acquisition_order, ("row", "header", "note"))
        self.assertEqual(result.observed_witness_ids, ("a-value", "a-binding", "not-b"))
        self.assertEqual(result.scope.split(";")[0], "online charged acquisition")

    def test_conflict_stops_before_any_later_action(self):
        called = []
        obligations = (Obligation("yes", "a", "value", ("row",)),
                       Obligation("no", "a", "value", ("row",)))

        def acquire(visible):
            called.append(visible.received_atoms)
            return ("row", "header")

        def verifier(visible, pending):
            signs = {"yes": "+", "no": "-"}
            return tuple(Judgment(item.obligation_id, signs[item.obligation_id]) for item in pending)

        result = run_acquisition(problem(), obligations, acquire, verifier, cost)
        self.assertEqual(result.stop_reason, CONFLICT)
        self.assertFalse(result.authorized)
        self.assertEqual(result.acquisition_order, ("row",))
        self.assertEqual(called, [()])
        self.assertEqual(result.evaluation.conflicts, (("a", "value"),))
        self.assertEqual(result.observed_witness_ids, ("no", "yes"))

    def test_verifier_cannot_answer_an_obligation_that_was_not_pending(self):
        def verifier(visible, pending):
            return tuple(Judgment(item.obligation_id, "U") for item in pending) + (Judgment("extra", "U"),)

        with self.assertRaises(ControllerError):
            run_acquisition(problem(), OBLIGATIONS, planner, verifier, cost)

    def test_verifier_must_answer_every_pending_obligation(self):
        with self.assertRaises(ControllerError):
            run_acquisition(problem(), OBLIGATIONS, planner, lambda visible, pending: (), cost)

    def test_preloaded_witness_is_rejected(self):
        planted = problem(witnesses=(Witness("planted", "a", "value", "+", ("row",), "Smuggled."),))
        with self.assertRaises(ControllerError):
            run_acquisition(planted, OBLIGATIONS, planner, honest, cost)

    def test_over_budget_action_is_not_acquired_or_verified(self):
        def expensive(selected, received):
            return Cost(5 * len(selected), UNIT, PROVENANCE)

        def verifier(visible, pending):
            raise AssertionError("verifier was called for an unaffordable action")

        result = run_acquisition(problem(), OBLIGATIONS, planner, verifier, expensive,
                                 budget=4, budget_unit=UNIT)
        self.assertEqual(result.stop_reason, BUDGET_EXCEEDED)
        self.assertEqual(result.acquisition_order, ())
        self.assertEqual(result.observed_witness_ids, ())
        self.assertIsNone(result.steps[0].acquired_action_id)
        self.assertEqual(result.steps[0].verifier_request_ids, ())

    def test_initial_atoms_are_judged_before_the_planner_runs(self):
        def acquire(visible):
            raise AssertionError("planner ran after the answer was already certified")

        result = run_acquisition(problem(initial=("row", "header", "note")), OBLIGATIONS,
                                 acquire, honest, cost)
        self.assertEqual(result.stop_reason, AUTHORIZED)
        self.assertEqual(result.steps, ())
        self.assertEqual(result.initial_verifier_request_ids, ("a-binding", "a-value", "not-b"))

    def test_unknown_action_raises_before_acquisition(self):
        with self.assertRaises(ControllerError):
            run_acquisition(problem(), OBLIGATIONS, lambda visible: ("missing",), honest, cost)

    def test_empty_proposal_stops_without_a_new_read(self):
        result = run_acquisition(problem(), OBLIGATIONS, lambda visible: (), unresolved, cost)
        self.assertEqual(result.stop_reason, PLANNER_STOP)
        self.assertEqual(result.acquisition_order, ())
        self.assertIsNone(result.steps[0].acquired_action_id)
        self.assertEqual(result.initial_verifier_request_ids, ())

    def test_external_gate_is_the_only_way_to_clear_coverage(self):
        held = run_acquisition(problem(cleared=False), OBLIGATIONS, planner, honest, cost,
                               coverage_fn=lambda visible: CoverageAssertion(False, "Stays open."))
        self.assertEqual(held.stop_reason, COVERAGE_UNRESOLVED)
        self.assertFalse(held.authorized)

        opened = run_acquisition(
            problem(cleared=False), OBLIGATIONS, planner, honest, cost,
            coverage_fn=lambda visible: CoverageAssertion(True, "Caller cleared the sentinel."))
        self.assertEqual(opened.stop_reason, AUTHORIZED)
        self.assertEqual(opened.evaluation.coverage_cleared, True)
        self.assertEqual(opened.evaluation.blockers, ())

"""Authored counterexamples for the finite theory; not empirical QA results."""

from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import unittest

from temporal_state.certificate_v21 import (
    Action, Candidate, Cost, CoverageAssertion, Problem, SearchLimitError, Witness,
    additive_cost, evaluate, problem_from_dict, solve_exact,
)


ROOT = Path(__file__).resolve().parents[1]
UNIT = "authored_cost_units"
PROVENANCE = "Authored counter, not measured tokens."


def fixture_dict():
    return json.loads((ROOT / "data/theory_controls_v21.json").read_text())["problem"]


def fixture():
    return problem_from_dict(fixture_dict())


def counter(selected, received):
    return Cost(len(received), UNIT, PROVENANCE)


def full(problem=None, **kwargs):
    return evaluate(problem or fixture(), ("row", "header", "footnote"), counter, **kwargs)


def signed(wid, candidate, claim, sign, *atoms):
    return Witness(wid, candidate, claim, sign, tuple(atoms), "Authored signed judgment.")


class CertificateSemantics(unittest.TestCase):
    def test_conjunction_requires_every_premise(self):
        for selected in (("row",), ("row", "header"), ("row", "footnote")):
            with self.subTest(selected=selected):
                result = evaluate(fixture(), selected, counter)
                self.assertNotIn("a-binding", result.activated_witness_ids)
                self.assertFalse(result.authorized)
        self.assertTrue(full().authorized)

    def test_same_class_unresolved_alias_does_not_need_refutation_or_support(self):
        result = full()
        self.assertEqual(result.survivors, ("a", "a-alias"))
        self.assertEqual(result.supported_candidates, ("a",))
        self.assertEqual(result.certificate_candidates, ("a",))
        self.assertTrue(result.authorized)

    def test_refuting_one_rival_class_representative_is_insufficient(self):
        p = fixture()
        p = replace(p, witnesses=tuple(w for w in p.witnesses if w.witness_id != "not-b-two"))
        result = full(p)
        self.assertIn("a", result.supported_candidates)
        self.assertIn("b-two", result.survivors)
        self.assertFalse(result.authorized)
        self.assertIn("UNREFUTED_RIVAL_ACTION", result.blockers)

    def test_missing_claim_in_joint_answer_cannot_be_silently_dropped(self):
        p = fixture()
        p = replace(p, candidates=tuple(replace(h, required_claims=h.required_claims + ("second-version",))
                                       if h.candidate_id == "a" else h for h in p.candidates))
        result = full(p)
        self.assertFalse(result.authorized)
        self.assertIn("NO_COMPLETE_POSITIVE_SUPPORT", result.blockers)

    def test_absence_of_rivals_does_not_supply_positive_evidence(self):
        p = Problem(("x",), (Candidate("only", "answer", ("claim",)),), (), (),
                    CoverageAssertion(True, "Invented exhaustive singleton."),
                    "Authored atoms.", "Authored problem.")
        result = evaluate(p, (), counter)
        self.assertEqual(result.survivors, ("only",))
        self.assertFalse(result.authorized)

    def test_refuting_every_candidate_does_not_authorize_empty_survivors(self):
        p = Problem(("x",), (Candidate("only", "answer", ("claim",)),), (Action("a", ("x",)),),
                    (signed("no", "only", "claim", "-", "x"),),
                    CoverageAssertion(True, "Invented exhaustive singleton."),
                    "Authored atoms.", "Authored problem.")
        result = evaluate(p, ("a",), counter)
        self.assertEqual(result.survivors, ())
        self.assertIn("NO_SURVIVOR", result.blockers)
        self.assertFalse(result.authorized)

    def test_conflict_on_unchosen_candidate_blocks_globally(self):
        result = evaluate(fixture(), ("row", "header", "footnote", "poison"), counter)
        self.assertEqual(result.conflicts, (("b-one", "binding"),))
        self.assertIn(("b-one", "binding", "unresolved"), result.claim_states)
        self.assertIn("b-one", result.survivors)
        self.assertIn("CONFLICT", result.blockers)
        self.assertFalse(result.local_certificate)
        self.assertFalse(result.authorized)

    def test_extra_evidence_can_destroy_an_existing_certificate(self):
        self.assertTrue(full().local_certificate)
        enlarged = evaluate(fixture(), ("row", "header", "footnote", "poison"), counter)
        self.assertFalse(enlarged.local_certificate)

    def test_active_adverse_rule_cannot_be_hidden_by_selecting_different_bundle_name(self):
        p = fixture()
        p = replace(p, actions=p.actions + (Action("combined", ("value", "heading", "note", "poison")),))
        result = evaluate(p, ("combined",), counter)
        self.assertIn("conflicting-b-one", result.activated_witness_ids)
        self.assertFalse(result.authorized)

    def test_unavailable_premise_remains_unresolved(self):
        p = fixture()
        p = replace(p, atoms=p.atoms + ("unavailable-gap",), witnesses=tuple(
            replace(w, premise_atoms=w.premise_atoms + ("unavailable-gap",))
            if w.witness_id == "a-binding" else w for w in p.witnesses))
        self.assertFalse(full(p).authorized)

    def test_initial_evidence_participates_in_premises_and_cost(self):
        p = replace(fixture(), initial_atoms=("value",))
        result = evaluate(p, ("header", "footnote"), counter)
        self.assertTrue(result.authorized)
        self.assertEqual(result.cost.amount, 3)
        self.assertIn("value", result.received_atoms)

    def test_whole_bundle_is_not_partly_packed_to_meet_budget(self):
        p = replace(fixture(), actions=(Action("all", ("value", "heading", "note")),))
        result = evaluate(p, ("all",), counter, budget=2, budget_unit=UNIT)
        self.assertTrue(result.local_certificate)
        self.assertFalse(result.within_budget)
        self.assertFalse(result.authorized)
        self.assertEqual(result.cost.amount, 3)

    def test_overlapping_bundles_charge_shared_atom_once(self):
        p = replace(fixture(), actions=(Action("left", ("value", "heading")),
                                        Action("right", ("heading", "note"))))
        cost = additive_cost({a: 1 for a in p.atoms}, unit=UNIT, provenance=PROVENANCE)
        result = evaluate(p, ("left", "right"), cost)
        self.assertEqual(result.cost.amount, 3)
        self.assertTrue(result.authorized)

    def test_unknown_cannot_be_cleared_by_positive_support_and_rival_exhaustion(self):
        p = replace(fixture(), coverage=CoverageAssertion(False, "Coverage unestablished."))
        result = full(p)
        self.assertTrue(result.local_certificate)
        self.assertFalse(result.authorized)
        self.assertEqual(result.blockers, ("COVERAGE_UNRESOLVED",))


class ExactSearch(unittest.TestCase):
    def test_exhaustive_optimum_and_deterministic_ties(self):
        result = solve_exact(fixture(), counter)
        self.assertEqual(result.status, "OPTIMAL")
        self.assertEqual(result.evaluated_subsets, 32)
        self.assertEqual(result.best.selected_action_ids, ("footnote", "header", "row"))
        self.assertEqual(result.best.cost.amount, 3)

    def test_nonmonotone_extension_rescues_overbudget_certificate(self):
        def costs(selected, received):
            return Cost(2 if "bridge" in received else len(received), UNIT, PROVENANCE)
        base = evaluate(fixture(), ("row", "header", "footnote"), costs,
                        budget=2, budget_unit=UNIT)
        self.assertTrue(base.local_certificate)
        self.assertFalse(base.within_budget)
        result = solve_exact(fixture(), costs, budget=2, budget_unit=UNIT)
        self.assertEqual(result.evaluated_subsets, 32)
        self.assertEqual(result.best.selected_action_ids, ("bridge", "footnote", "header", "row"))
        self.assertTrue(result.best.authorized)

    def test_infeasible_status_requires_complete_enumeration(self):
        result = solve_exact(fixture(), counter, budget=2, budget_unit=UNIT)
        self.assertEqual(result.status, "INFEASIBLE")
        self.assertIsNone(result.best)
        self.assertEqual(result.evaluated_subsets, 32)

    def test_local_optimum_not_reported_as_authorized(self):
        p = replace(fixture(), coverage=CoverageAssertion(False, "Coverage unresolved."))
        authorized = solve_exact(p, counter)
        self.assertEqual(authorized.objective, "authorized_certificate")
        self.assertEqual(authorized.status, "INFEASIBLE")
        local = solve_exact(p, counter, require_coverage=False)
        self.assertEqual(local.objective, "local_certificate")
        self.assertEqual(local.status, "OPTIMAL")
        self.assertFalse(local.best.authorized)

    def test_cap_refusal_is_not_infeasibility_and_never_calls_cost(self):
        p = replace(fixture(), actions=tuple(Action(f"action{i}", ("value",)) for i in range(21)))
        calls = []
        def never(selected, received):
            calls.append(selected)
            return counter(selected, received)
        with self.assertRaises(SearchLimitError):
            solve_exact(p, never)
        self.assertEqual(calls, [])

    def test_explicit_lower_cap_refuses_without_false_result(self):
        with self.assertRaises(SearchLimitError):
            solve_exact(fixture(), counter, max_actions=4)

    def test_rational_costs_do_not_use_float_rounding(self):
        cost = additive_cost({a: Fraction(1, 3) for a in fixture().atoms}, unit=UNIT, provenance=PROVENANCE)
        result = solve_exact(fixture(), cost, budget=1, budget_unit=UNIT)
        self.assertEqual(result.best.cost.amount, Fraction(1))

    def test_zero_cost_ties_prefer_fewer_actions_then_lexical_ids(self):
        p = replace(fixture(), actions=(Action("z-full", ("value", "heading", "note")),
                                        Action("a-full", ("value", "heading", "note"))))
        result = solve_exact(p, lambda selected, received: Cost(0, UNIT, PROVENANCE))
        self.assertEqual(result.best.selected_action_ids, ("a-full",))
        self.assertEqual(result.evaluated_subsets, 4)

    def test_input_order_does_not_change_optimum(self):
        p = fixture()
        reordered = replace(p, atoms=p.atoms[::-1], actions=p.actions[::-1],
                            candidates=p.candidates[::-1], witnesses=p.witnesses[::-1])
        self.assertEqual(solve_exact(p, counter), solve_exact(reordered, counter))

    def test_zero_actions_and_initial_evidence_can_supply_certificate(self):
        p = replace(fixture(), actions=(), initial_atoms=("value", "heading", "note"))
        result = solve_exact(p, counter, max_actions=0)
        self.assertEqual(result.evaluated_subsets, 1)
        self.assertEqual(result.best.selected_action_ids, ())
        self.assertTrue(result.best.authorized)

    def test_changed_cost_unit_or_provenance_is_rejected(self):
        for changed in ("unit", "provenance"):
            def bad(selected, received):
                return Cost(len(received), "other" if changed == "unit" and selected else UNIT,
                            "other" if changed == "provenance" and selected else PROVENANCE)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                solve_exact(fixture(), bad)


class StrictContract(unittest.TestCase):
    def test_json_unknown_keys_and_wrong_container_types_are_rejected(self):
        for mutation in (lambda d: d.update(unknown=1),
                         lambda d: d.update(atoms="not-a-list"),
                         lambda d: d["actions"][0].update(unknown=1),
                         lambda d: d["coverage"].update(cleared="false")):
            data = fixture_dict()
            mutation(data)
            with self.assertRaises(ValueError):
                problem_from_dict(data)

    def test_duplicate_ids_and_empty_obligations_rejected(self):
        for constructor in (lambda: Candidate("a", "class", ()),
                            lambda: Candidate("a", "class", ("x", "x")),
                            lambda: Action("a", ("x", "x")),
                            lambda: Witness("w", "a", "x", "+", (), "Authored."),
                            lambda: replace(fixture(), candidates=fixture().candidates * 2),
                            lambda: replace(fixture(), witnesses=fixture().witnesses * 2)):
            with self.assertRaises(ValueError):
                constructor()

    def test_unknown_atoms_candidates_claims_are_rejected(self):
        p = fixture()
        for changes in ({"initial_atoms": ("missing",)},
                        {"actions": (Action("a", ("missing",)),)},
                        {"witnesses": (signed("w", "missing", "value", "+", "value"),)},
                        {"witnesses": (signed("w", "a", "missing", "+", "value"),)},
                        {"witnesses": (signed("w", "a", "value", "+", "missing"),)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(p, **changes)

    def test_unresolved_is_not_a_negative_rule(self):
        with self.assertRaises(ValueError):
            signed("w", "a", "value", "unresolved", "value")

    def test_missing_provenance_rejected(self):
        for constructor in (lambda: CoverageAssertion(True, ""),
                            lambda: Cost(1, UNIT, ""),
                            lambda: replace(fixture(), atom_provenance=""),
                            lambda: replace(fixture().witnesses[0], provenance="")):
            with self.assertRaises(ValueError):
                constructor()

    def test_invalid_costs_rejected(self):
        for amount in (-1, True, 0.1, float("nan"), float("inf"), "1"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                Cost(amount, UNIT, PROVENANCE)

    def test_budget_requires_matching_explicit_unit(self):
        for kwargs in ({"budget": 3}, {"budget_unit": UNIT},
                       {"budget": 3, "budget_unit": "wrong"},
                       {"budget": True, "budget_unit": UNIT}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                full(**kwargs)

    def test_selection_rejects_unknown_or_duplicate_ids(self):
        for selected in (("unknown",), ("row", "row")):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                evaluate(fixture(), selected, counter)

    def test_invalid_solver_configuration_rejected(self):
        for kwargs in ({"require_coverage": 1}, {"max_actions": True},
                       {"max_actions": -1}, {"max_actions": 21}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                solve_exact(fixture(), counter, **kwargs)

    def test_additive_cost_charges_action_and_fixed_overhead(self):
        p = fixture()
        cost = additive_cost({a: 1 for a in p.atoms}, unit=UNIT, provenance=PROVENANCE,
                             action_costs={a.action_id: 2 for a in p.actions}, fixed_cost=7)
        self.assertEqual(evaluate(p, ("row", "header", "footnote"), cost).cost.amount, 16)

    def test_additive_cost_rejects_unpriced_received_atoms(self):
        cost = additive_cost({}, unit=UNIT, provenance=PROVENANCE)
        with self.assertRaises(ValueError):
            evaluate(fixture(), ("row",), cost)


if __name__ == "__main__":
    unittest.main()

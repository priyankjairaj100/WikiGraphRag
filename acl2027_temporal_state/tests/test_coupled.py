"""Conditional STN checks; these are implementation diagnostics, not results."""
from dataclasses import replace
from datetime import date
from itertools import product
import unittest

from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.coupled import (
    TemporalInfeasible, make_feasibility_check, materialize_selection,
    selection_feasible,
)
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Question, Source


def fixture(records, relations=()):
    sources, mentions, claims = [], [], {}
    for i, original in enumerate(records):
        fields = dict(original)
        mid, sid = f"m{i}", f"s{i}"
        value = fields.pop("value", "A")
        text = f"{value} has the role."
        sources.append(Source(sid, text, fields.pop("available_at", "2024-12-31")))
        record = dict(claim_id=f"c{i}", source_id=sid, subject="Org", relation="CEO", value=value,
                      evidence_spans=(EvidenceSpan(0, len(text), text),), reported_at="2024-12-31")
        record.update(fields)
        claim = TemporalClaim(**record)
        claims[mid, "r"] = claim
        mentions.append(Mention(mid, sid, 0, len(text), (Reading("r", ("Org", "CEO", ""), value, 1),
                                                       Reading("u", None, None, 0))))
    links = tuple(Link(f"l{i}", f"m{later}", "r", f"m{earlier}", "r", relation, 1)
                  for i, (later, earlier, relation) in enumerate(relations))
    problem = Problem("2024-12-31", tuple(sources), tuple(mentions), links, "authored diagnostic")
    reading_ids = {mention.mention_id: "r" for mention in mentions}
    link_ids = {mention.mention_id: None for mention in mentions}
    for link in links:
        link_ids[link.mention_id] = link.link_id
    return problem, reading_ids, link_ids, claims


def bounds(a=None, b=None):
    return DayBounds(a, b)


def answer(memory, day, mode="announced_schedule"):
    return memory.answer_key(Question("q", "Who is CEO of Org?", day, memory.cutoff),
                             ("Org", "CEO", ""), mode)


class CoupledSemanticsTests(unittest.TestCase):
    def test_restates_combines_observations_only_when_linked(self):
        args = fixture([{"state_observed_at": ("2024-01-02",)},
                        {"state_observed_at": ("2024-01-08",)}], [(1, 0, "RESTATES")])
        linked = materialize_selection(*args)
        unlinked = materialize_selection(args[0], args[1], {"m0": None, "m1": None}, args[3])
        self.assertEqual(answer(linked, "2024-01-05").values, ("A",))
        self.assertEqual(answer(unlinked, "2024-01-05").status, "indeterminate")
        self.assertIsNone(linked.envelopes["c0"].start_lower)
        self.assertIsNone(linked.envelopes["c0"].end_upper)
        self.assertEqual(linked.claims[0].state_observed_at, ("2024-01-02",))
        self.assertEqual(linked.diagnostics["restatement_components"], [["m0", "m1"]])

    def test_changes_can_exclude_unknown_start_competitor(self):
        args = fixture([{"start": bounds("2024-01-01", "2024-01-01"),
                         "end": bounds("2024-01-05", "2024-01-05")},
                        {"value": "B", "state_observed_at": ("2024-01-08",)}], [(1, 0, "CHANGES")])
        linked = materialize_selection(*args)
        unlinked = materialize_selection(args[0], args[1], {"m0": None, "m1": None}, args[3])
        self.assertEqual(answer(linked, "2024-01-03").values, ("A",))
        self.assertEqual(answer(unlinked, "2024-01-03").status, "indeterminate")
        self.assertEqual(linked.envelopes["c1"].start_lower, date(2024, 1, 5).toordinal())
        self.assertEqual(answer(linked, "2024-01-03").evidence_source_ids, ("s0", "s1"))

    def test_changes_does_not_infer_adjacency(self):
        args = fixture([{"end": bounds("2024-01-03", "2024-01-03")},
                        {"value": "B", "start": bounds("2024-01-08", "2024-01-08")}], [(1, 0, "CHANGES")])
        memory = materialize_selection(*args)
        self.assertEqual(memory.envelopes["c0"].end_upper, date(2024, 1, 3).toordinal())
        self.assertEqual(memory.envelopes["c1"].start_lower, date(2024, 1, 8).toordinal())
        self.assertEqual(answer(memory, "2024-01-05").status, "abstain")

    def test_unknown_unlinked_intervals_can_overlap(self):
        args = fixture([{"state_observed_at": ("2024-01-05",)},
                        {"value": "B", "state_observed_at": ("2024-01-05",)}])
        self.assertTrue(selection_feasible(*args))
        self.assertEqual(answer(materialize_selection(*args), "2024-01-05").status, "indeterminate")

    def test_changes_cycle_rejected_even_without_dates(self):
        args = fixture([{}, {"value": "B"}, {"value": "C"}],
                       [(1, 0, "CHANGES"), (2, 1, "CHANGES"), (0, 2, "CHANGES")])
        self.assertFalse(selection_feasible(*args))
        with self.assertRaisesRegex(TemporalInfeasible, "Negative cycle"):
            materialize_selection(*args)

    def test_restates_bound_conflict_rejected(self):
        args = fixture([{"start": bounds("2024-01-01", "2024-01-01")},
                        {"start": bounds("2024-01-02", "2024-01-02")}], [(1, 0, "RESTATES")])
        self.assertFalse(selection_feasible(*args))

    def test_observation_order_conflict_rejected(self):
        args = fixture([{"state_observed_at": ("2024-01-08",)},
                        {"value": "B", "state_observed_at": ("2024-01-05",)}], [(1, 0, "CHANGES")])
        self.assertFalse(selection_feasible(*args))

    def test_reversion_keeps_distinct_a_episodes(self):
        args = fixture([{"start": bounds("2024-01-01", "2024-01-01"), "end": bounds("2024-01-03", "2024-01-03")},
                        {"value": "B", "start": bounds("2024-01-04", "2024-01-04"), "end": bounds("2024-01-06", "2024-01-06")},
                        {"start": bounds("2024-01-07", "2024-01-07"), "end": bounds("2024-01-09", "2024-01-09")}],
                       [(1, 0, "CHANGES"), (2, 1, "CHANGES")])
        memory = materialize_selection(*args)
        self.assertEqual(memory.diagnostics["restatement_components"], [["m0"], ["m1"], ["m2"]])
        self.assertEqual(answer(memory, "2024-01-05").values, ("B",))
        self.assertEqual(answer(memory, "2024-01-06").status, "abstain")
        self.assertEqual(answer(memory, "2024-01-08").values, ("A",))

    def test_negative_claims_cannot_be_change_endpoints(self):
        for negative in (0, 1):
            records = [{}, {"value": "B"}]
            records[negative]["polarity"] = "negative"
            self.assertFalse(selection_feasible(*fixture(records, [(1, 0, "CHANGES")])))

    def test_restates_requires_equal_polarity(self):
        self.assertFalse(selection_feasible(*fixture([{}, {"polarity": "negative"}], [(1, 0, "RESTATES")])))
        args = fixture([{"polarity": "negative"}, {"polarity": "negative"}], [(1, 0, "RESTATES")])
        self.assertTrue(selection_feasible(*args))
        self.assertEqual(answer(materialize_selection(*args), "2024-01-05").status, "abstain")

    def test_no_link_can_mix_actual_and_planned_modalities(self):
        for relation, value in (("RESTATES", "A"), ("CHANGES", "B")):
            args = fixture([{}, {"value": value, "modality": "announced_future"}], [(1, 0, relation)])
            self.assertFalse(selection_feasible(*args))

    def test_planned_component_is_excluded_from_actual_queries(self):
        args = fixture([{"modality": "announced_future", "start": bounds("2024-01-02", "2024-01-02")},
                        {"modality": "announced_future", "end": bounds("2024-01-09", "2024-01-09")}], [(1, 0, "RESTATES")])
        memory = materialize_selection(*args)
        self.assertEqual(answer(memory, "2024-01-05").status, "answered")
        self.assertEqual(answer(memory, "2024-01-05", "reported_actual").status, "abstain")

    def test_actual_report_horizon_is_not_raised_by_later_change(self):
        args = fixture([{"reported_at": "2024-01-02", "start": bounds("2024-01-01", "2024-01-01"),
                         "end": bounds("2024-01-10", "2024-01-10")},
                        {"value": "B", "reported_at": "2024-01-10", "state_observed_at": ("2024-01-10",),
                         "start": bounds("2024-01-10", "2024-01-10")}], [(1, 0, "CHANGES")])
        memory = materialize_selection(*args)
        self.assertEqual(answer(memory, "2024-01-05").status, "answered")
        actual = answer(memory, "2024-01-05", "reported_actual")
        self.assertEqual(actual.status, "abstain")
        self.assertEqual(actual.diagnostics["actual_horizons"]["c0"]["date"], "2024-01-02")

    def test_later_actual_restatement_can_support_retrospective_interval(self):
        args = fixture([{"reported_at": "2024-01-02", "state_observed_at": ("2024-01-02",)},
                        {"reported_at": "2024-01-08", "state_observed_at": ("2024-01-08",)}], [(1, 0, "RESTATES")])
        result = answer(materialize_selection(*args), "2024-01-05", "reported_actual")
        self.assertEqual(result.values, ("A",))
        self.assertEqual(result.evidence_assertion_ids, ("c1",))
        self.assertEqual(result.evidence_source_ids, ("s0", "s1"))
        self.assertEqual(result.diagnostics["excluded_actual_projection_claim_ids"], ["c0"])

    def test_uncertain_component_never_becomes_certain(self):
        args = fixture([{"modality": "uncertain", "state_observed_at": ("2024-01-02",)},
                        {"modality": "uncertain", "state_observed_at": ("2024-01-08",)}], [(1, 0, "RESTATES")])
        self.assertEqual(answer(materialize_selection(*args), "2024-01-05").status, "indeterminate")

    def test_all_connected_context_dependencies_are_cited(self):
        problem, readings, links, claims = fixture([{"state_observed_at": ("2024-01-02",)},
                                                   {"state_observed_at": ("2024-01-08",)}], [(1, 0, "RESTATES")])
        extra = tuple(Source(f"x{i}", "Context.", "2024-12-31") for i in range(5))
        first = problem.mentions[0]
        first = replace(first, context_source_ids=("x1",),
                        readings=(replace(first.readings[0], context_source_ids=("x2",)), first.readings[1]))
        problem = replace(problem, sources=problem.sources + extra, context_source_ids=("x0",),
                          mentions=(first, problem.mentions[1]),
                          links=(replace(problem.links[0], context_source_ids=("x3",)),))
        claims["m0", "r"] = replace(claims["m0", "r"], context_source_ids=("x4",))
        result = answer(materialize_selection(problem, readings, links, claims), "2024-01-05")
        self.assertEqual(result.evidence_source_ids, ("s0", "s1", "x0", "x1", "x2", "x3", "x4"))

    def test_correction_selected_is_unsupported_but_null_is_feasible(self):
        args = fixture([{}, {}], [(1, 0, "CORRECTS")])
        self.assertFalse(selection_feasible(*args))
        self.assertTrue(selection_feasible(args[0], args[1], {"m0": None, "m1": None}, args[3]))

    def test_unresolved_reading_creates_no_claim(self):
        problem, readings, links, claims = fixture([{}, {}])
        memory = materialize_selection(problem, {"m0": "u", "m1": "r"}, links, claims)
        self.assertEqual([claim.claim_id for claim in memory.claims], ["c1"])

    def test_date_edge_internal_ordinals_need_no_invented_iso_dates(self):
        problem, readings, links, claims = fixture([
            {"reported_at": "9999-12-31", "available_at": "9999-12-31", "state_observed_at": ("9999-12-31",)},
            {"value": "B", "reported_at": "9999-12-31", "available_at": "9999-12-31"}], [(1, 0, "CHANGES")])
        problem = replace(problem, cutoff="9999-12-31")
        memory = materialize_selection(problem, readings, links, claims)
        self.assertEqual(memory.envelopes["c1"].start_lower, date.max.toordinal() + 1)
        self.assertEqual(answer(memory, "9999-12-31").values, ("A",))

    def test_answer_certainty_is_explicitly_conditional_on_selected_state(self):
        memory = materialize_selection(*fixture([{"state_observed_at": ("2024-01-02",)}]))
        diagnostic = answer(memory, "2024-01-02").diagnostics
        self.assertFalse(diagnostic["alternative_assignment_agreement_certified"])
        self.assertIn("selected_readings_links", diagnostic["certainty_scope"])


class CoupledValidationTests(unittest.TestCase):
    def test_malformed_selection_ids_raise_instead_of_becoming_infeasible(self):
        p, r, l, c = fixture([{}])
        with self.assertRaises(ValueError):
            selection_feasible(p, {"m0": "missing"}, l, c)
        with self.assertRaises(ValueError):
            selection_feasible(p, r, {}, c)
        with self.assertRaises(ValueError):
            selection_feasible(p, r, {"m0": "missing"}, c)

    def test_complete_claim_map_required_and_null_cannot_map_claim(self):
        p, r, l, c = fixture([{}])
        with self.assertRaisesRegex(ValueError, "exactly every resolved"):
            selection_feasible(p, r, l, {})
        c["m0", "u"] = c["m0", "r"]
        with self.assertRaisesRegex(ValueError, "exactly every resolved"):
            selection_feasible(p, r, l, c)

    def test_candidate_identity_and_exact_dates_must_match_claim(self):
        p, r, l, c = fixture([{}])
        c["m0", "r"] = replace(c["m0", "r"], value="wrong")
        with self.assertRaisesRegex(ValueError, "must match"):
            selection_feasible(p, r, l, c)
        p, r, l, c = fixture([{}])
        mention = p.mentions[0]
        p = replace(p, mentions=(replace(mention, readings=(replace(mention.readings[0], effective_start="2024-01-01"), mention.readings[1])),))
        with self.assertRaisesRegex(ValueError, "Exact reading endpoint"):
            selection_feasible(p, r, l, c)

    def test_unselected_candidate_evidence_still_validated(self):
        p, r, l, c = fixture([{}])
        c["m0", "r"] = replace(c["m0", "r"], evidence_spans=(EvidenceSpan(0, 3, "bad"),))
        with self.assertRaisesRegex(ValueError, "Nonmatching evidence"):
            selection_feasible(p, {"m0": "u"}, l, c)

    def test_future_source_and_future_declared_context_fail_closed(self):
        p, r, l, c = fixture([{}])
        p = replace(p, cutoff="2024-01-01")
        with self.assertRaisesRegex(ValueError, "available in this prefix"):
            selection_feasible(p, r, l, c)
        p, r, l, c = fixture([{}])
        p = replace(p, context_source_ids=("future_missing",))
        with self.assertRaisesRegex(ValueError, "eligible prefix"):
            selection_feasible(p, r, l, c)

    def test_hook_uses_frozen_candidate_map_and_validates_callback_objects(self):
        p, r, l, c = fixture([{}, {"value": "B"}], [(1, 0, "CHANGES")])
        hook = make_feasibility_check(p, c)
        selected = {m.mention_id: m.readings[0] for m in p.mentions}
        chosen = (None, p.links[0])
        c.clear()
        self.assertTrue(hook(selected, chosen))
        selected["m0"] = replace(selected["m0"], value="changed")
        with self.assertRaisesRegex(ValueError, "differs from fixed"):
            hook(selected, chosen)

    def test_mutable_claim_fields_rejected_before_hook_cache(self):
        p, r, l, c = fixture([{}])
        c["m0", "r"] = replace(c["m0", "r"], state_observed_at=[])
        with self.assertRaisesRegex(ValueError, "immutable tuples"):
            make_feasibility_check(p, c)

    def test_mutable_problem_and_candidate_collections_rejected(self):
        p, r, l, c = fixture([{}, {}], [(1, 0, "RESTATES")])
        invalid = [replace(p, sources=list(p.sources)), replace(p, context_source_ids=[]),
                   replace(p, mentions=(replace(p.mentions[0], context_source_ids=[]), p.mentions[1])),
                   replace(p, links=(replace(p.links[0], reference_span=[0, 1]),))]
        mention = p.mentions[0]
        invalid.append(replace(p, mentions=(replace(mention, readings=(replace(mention.readings[0], key=["Org", "CEO", ""]), mention.readings[1])), p.mentions[1])))
        for problem in invalid:
            with self.assertRaisesRegex(ValueError, "immutable tuple"):
                make_feasibility_check(problem, c)


class CoupledFiniteOracleTests(unittest.TestCase):
    def test_closure_matches_exhaustive_small_integer_models(self):
        days = [f"2024-01-0{i + 1}" for i in range(3)]
        ordinal0 = date(2024, 1, 1).toordinal()
        cases = []
        for sl, su, el, eu in product(range(3), repeat=4):
            if sl > su or el > eu:
                continue
            models = [(s, e) for s in range(sl, su + 1) for e in range(el, eu + 1) if s < e]
            if models:
                cases.append(({"start": bounds(days[sl], days[su]), "end": bounds(days[el], days[eu])}, models))
        compared = 0
        for relation in ("RESTATES", "CHANGES"):
            for (first, first_models), (second, second_models) in product(cases, repeat=2):
                second = dict(second, value="A" if relation == "RESTATES" else "B")
                models = [(a, b) for a, b in product(first_models, second_models)
                          if (a == b if relation == "RESTATES" else a[1] <= b[0])]
                args = fixture([first, second], [(1, 0, relation)])
                self.assertEqual(selection_feasible(*args), bool(models))
                if not models:
                    continue
                memory = materialize_selection(*args)
                for i in range(2):
                    envelope = memory.envelopes[f"c{i}"]
                    expected = (min(m[i][0] for m in models), max(m[i][0] for m in models),
                                min(m[i][1] for m in models), max(m[i][1] for m in models))
                    self.assertEqual((envelope.start_lower, envelope.start_upper, envelope.end_lower, envelope.end_upper),
                                     tuple(ordinal0 + endpoint for endpoint in expected))
                    for t, day in enumerate(days):
                        outcomes = [model[i][0] <= t < model[i][1] for model in models]
                        membership = envelope.membership(day)
                        self.assertEqual((membership.may, membership.must), (any(outcomes), all(outcomes)))
                        compared += 1
        self.assertGreater(compared, 1000)


if __name__ == "__main__":
    unittest.main()

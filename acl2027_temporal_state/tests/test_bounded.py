"""Contract checks for bounds, query semantics, and declared prefix provenance."""
import itertools
import json
import unittest
from datetime import date, timedelta

from temporal_state.bounded import (
    DayBounds, EvidenceSpan, TemporalClaim, build_bounded_memory,
    claim_from_dict, claim_membership, claim_to_dict, materialize_claim,
)
from temporal_state.models import Question, Source


QUOTE = "A has the role."
SOURCE = Source("s1", QUOTE, "2024-12-31")
SPAN = EvidenceSpan(0, len(QUOTE), QUOTE)


def claim(**kwargs):
    fields = dict(claim_id="c1", source_id="s1", subject="Org", relation="CEO", value="A", evidence_spans=(SPAN,))
    fields.update(kwargs)
    return TemporalClaim(**fields)


def answer(claims, day="2024-01-02", sources=(SOURCE,), cutoff="2024-12-31", mode="announced_schedule"):
    memory = build_bounded_memory(sources, claims, cutoff)
    question = Question("q", "Who is CEO of Org?", day, cutoff)
    return memory.answer_key(question, ("Org", "CEO", ""), mode)


class BoundsTests(unittest.TestCase):
    def test_exact_start_guarantees_only_start_without_persistence(self):
        c = claim(start=DayBounds("2024-01-02", "2024-01-02"))
        self.assertEqual(claim_membership(c, "2024-01-02").must, True)
        self.assertEqual(claim_membership(c, "2024-01-03").must, False)
        self.assertEqual(claim_membership(c, "2024-01-03").may, True)
        self.assertFalse(claim_membership(c, "2024-01-01").may)

    def test_observation_does_not_invent_start(self):
        c = claim(state_observed_at=("2024-06-10",))
        self.assertEqual(c.start, DayBounds())
        self.assertTrue(claim_membership(c, "2024-06-10").must)
        self.assertFalse(claim_membership(c, "2024-06-09").must)
        self.assertTrue(claim_membership(c, "2024-06-09").may)
        self.assertEqual(answer([c], "2024-06-10").status, "answered")

    def test_multiple_observations_constrain_same_interval(self):
        c = claim(state_observed_at=("2024-06-01", "2024-06-10"))
        self.assertTrue(claim_membership(c, "2024-06-05").must)
        self.assertFalse(claim_membership(c, "2024-05-31").must)

    def test_year_bound_does_not_choose_january_first(self):
        c = claim(start=DayBounds("2023-01-01", "2023-12-31"), end=DayBounds("2025-01-01", "2025-01-01"))
        self.assertFalse(claim_membership(c, "2022-12-31").may)
        self.assertTrue(claim_membership(c, "2023-01-01").may)
        self.assertFalse(claim_membership(c, "2023-01-01").must)
        self.assertTrue(claim_membership(c, "2023-12-31").must)
        self.assertFalse(claim_membership(c, "2025-01-01").may)

    def test_end_uncertainty_is_local(self):
        c = claim(start=DayBounds("2024-01-01", "2024-01-01"), end=DayBounds("2024-10-13", "2024-10-14"))
        self.assertTrue(claim_membership(c, "2024-10-12").must)
        self.assertTrue(claim_membership(c, "2024-10-13").may)
        self.assertFalse(claim_membership(c, "2024-10-13").must)
        self.assertFalse(claim_membership(c, "2024-10-14").may)

    def test_unknown_is_possible_not_empty(self):
        membership = claim_membership(claim(), "2024-01-01")
        self.assertTrue(membership.may)
        self.assertFalse(membership.must)

    def test_cross_bounds_enforce_positive_duration(self):
        with self.assertRaisesRegex(ValueError, "positive-duration"):
            claim(start=DayBounds("2024-02-01", None), end=DayBounds(None, "2024-02-01"))
        c = claim(start=DayBounds("2024-01-02", "2024-01-05"), end=DayBounds("2024-01-01", "2024-01-03"))
        self.assertTrue(claim_membership(c, "2024-01-02").must)
        self.assertEqual(materialize_claim(c).start_upper, date(2024, 1, 2).toordinal())

    def test_observation_at_exact_end_is_impossible(self):
        with self.assertRaisesRegex(ValueError, "positive-duration"):
            claim(end=DayBounds("2024-05-01", "2024-05-01"), state_observed_at=("2024-05-01",))

    def test_observation_before_earliest_start_is_impossible(self):
        with self.assertRaisesRegex(ValueError, "positive-duration"):
            claim(start=DayBounds("2024-05-02", None), state_observed_at=("2024-05-01",))

    def test_date_edges_allow_internal_adjacent_ordinal(self):
        c = claim(state_observed_at=("9999-12-31",))
        self.assertTrue(claim_membership(c, "9999-12-31").must)
        self.assertEqual(materialize_claim(c).end_lower, date.max.toordinal() + 1)

    def test_endpoint_validation(self):
        for bounds in [("2024-01-02", "2024-01-01"), ("2024-1-1", None), ("2024-02-30", None)]:
            with self.assertRaises(ValueError):
                DayBounds(*bounds)

    def test_analytic_membership_matches_exhaustive_finite_models(self):
        base = date(2024, 1, 1)
        days = [(base + timedelta(days=i)).isoformat() for i in range(5)]
        for sl, su, el, eu in itertools.product(range(5), repeat=4):
            if sl > su or el > eu:
                continue
            models = [(s, e) for s in range(sl, su + 1) for e in range(el, eu + 1) if s < e]
            kwargs = dict(start=DayBounds(days[sl], days[su]), end=DayBounds(days[el], days[eu]))
            if not models:
                with self.assertRaises(ValueError):
                    claim(**kwargs)
                continue
            c = claim(**kwargs)
            for t, day in enumerate(days):
                membership = claim_membership(c, day)
                outcomes = [s <= t < e for s, e in models]
                self.assertEqual((membership.may, membership.must), (any(outcomes), all(outcomes)), (sl, su, el, eu, t))


class QueryTests(unittest.TestCase):
    def test_planned_negative_round_trip_and_no_positive_occupancy(self):
        c = claim(polarity="negative", modality="announced_future", start=DayBounds("2024-01-02", "2024-01-02"), reported_at="2024-01-01")
        self.assertEqual(claim_from_dict(json.loads(json.dumps(claim_to_dict(c)))), c)
        self.assertEqual(answer([c]).status, "abstain")
        self.assertEqual(answer([c], mode="reported_actual").diagnostics["excluded_plan_claim_ids"], ["c1"])

    def test_plans_are_query_mode_explicit(self):
        c = claim(modality="announced_future", start=DayBounds("2024-01-02", "2024-01-02"))
        planned = answer([c])
        self.assertEqual(planned.status, "answered")
        self.assertEqual(planned.diagnostics["supporting_claim_modalities"], {"c1": "announced_future"})
        self.assertEqual(answer([c], mode="reported_actual").status, "abstain")

    def test_future_scheduled_end_does_not_imply_actual_future_occupancy(self):
        c = claim(reported_at="2024-01-01", state_observed_at=("2024-01-01",), end=DayBounds("2024-02-01", "2024-02-01"))
        self.assertEqual(answer([c], "2024-01-08", mode="announced_schedule").status, "answered")
        result = answer([c], "2024-01-08", mode="reported_actual")
        self.assertEqual(result.status, "abstain")
        self.assertEqual(result.diagnostics["excluded_actual_projection_claim_ids"], ["c1"])
        self.assertEqual(result.diagnostics["actual_horizons"]["c1"], {"date": "2024-01-01", "basis": "reported_at"})
        self.assertEqual(answer([c], "2024-01-01", mode="reported_actual").status, "answered")

    def test_actual_projection_without_report_date_uses_availability_fallback(self):
        source = Source("s1", QUOTE, "2024-01-01")
        c = claim(start=DayBounds("2024-01-01", "2024-01-01"), end=DayBounds("2024-02-01", "2024-02-01"))
        result = answer([c], "2024-01-02", sources=(source,), mode="reported_actual")
        self.assertEqual(result.status, "abstain")
        self.assertEqual(result.diagnostics["actual_horizons"]["c1"]["basis"], "source_available_at_fallback")

    def test_conditional_and_uncertain_never_supply_certainty(self):
        for fields in ({"modality": "conditional"}, {"modality": "uncertain"}):
            c = claim(state_observed_at=("2024-01-02",), **fields)
            self.assertEqual(answer([c]).status, "indeterminate")

    def test_unresolved_update_link_does_not_erase_observed_state(self):
        c = claim(operation="UNRESOLVED", reported_at="2024-01-02", state_observed_at=("2024-01-02",))
        self.assertEqual(answer([c], mode="reported_actual").status, "answered")
        uncertain = claim(operation="UNRESOLVED", modality="uncertain", reported_at="2024-01-02", state_observed_at=("2024-01-02",))
        self.assertEqual(answer([uncertain], mode="reported_actual").status, "indeterminate")

    def test_later_competing_observation_has_no_cross_claim_constraint_propagation(self):
        early = claim(state_observed_at=("2024-01-02",))
        later = claim(claim_id="c2", value="B", state_observed_at=("2024-11-20",))
        result = answer([early, later], "2024-01-02")
        self.assertEqual(result.status, "indeterminate")
        self.assertEqual(result.diagnostics["cross_claim_constraint_propagation"], "none; selected_intervals_checked_independently")
        self.assertEqual(result.diagnostics["persistence_assumption"], "connected_interval_within_each_selected_episode; no_forward_persistence")

    def test_possible_competitor_blocks_certain_answer(self):
        certain = claim(state_observed_at=("2024-01-02",))
        possible = claim(claim_id="c2", value="B")
        self.assertEqual(answer([certain, possible]).status, "indeterminate")

    def test_late_samevalue_observation_not_cited_for_earlier_state(self):
        early = claim(state_observed_at=("2024-01-02",))
        later_source = Source("s2", QUOTE, "2024-11-20")
        later = claim(claim_id="c2", source_id="s2", state_observed_at=("2024-11-20",))
        result = answer([early, later], sources=(SOURCE, later_source))
        self.assertEqual(result.status, "answered")
        self.assertEqual(result.evidence_source_ids, ("s1",))
        self.assertEqual(result.evidence_assertion_ids, ("c1",))

    def test_certain_negative_blocks_merely_possible_samevalue(self):
        possible = claim()
        negative = claim(claim_id="c2", polarity="negative", state_observed_at=("2024-01-02",))
        result = answer([possible, negative])
        self.assertEqual(result.status, "abstain")
        self.assertEqual(result.diagnostics["blocked_possible_value_keys"], ["a"])

    def test_certain_positive_possible_negative_is_conflict(self):
        positive = claim(state_observed_at=("2024-01-02",))
        negative = claim(claim_id="c2", polarity="negative")
        self.assertEqual(answer([positive, negative]).status, "indeterminate")

    def test_certain_positive_certain_negative_is_conflict(self):
        positive = claim(state_observed_at=("2024-01-02",))
        negative = claim(claim_id="c2", polarity="negative", state_observed_at=("2024-01-02",))
        self.assertEqual(answer([positive, negative]).status, "indeterminate")

    def test_blocked_competitor_does_not_block_supported_different_value(self):
        positive = claim(state_observed_at=("2024-01-02",))
        other = claim(claim_id="c2", value="B")
        negative = claim(claim_id="c3", value="B", polarity="negative", state_observed_at=("2024-01-02",))
        result = answer([positive, other, negative])
        self.assertEqual(result.values, ("A",))
        self.assertEqual(result.evidence_assertion_ids, ("c1", "c3"))

    def test_possible_negative_cannot_establish_an_alternative(self):
        self.assertEqual(answer([claim(polarity="negative")]).status, "abstain")

    def test_outside_bounded_interval_has_no_spurious_uncertainty(self):
        c = claim(end=DayBounds("2024-01-01", "2024-01-01"))
        self.assertEqual(answer([c]).status, "abstain")

    def test_scope_and_predicted_key_are_respected(self):
        c = claim(scope="interim", state_observed_at=("2024-01-02",))
        self.assertEqual(answer([c]).status, "abstain")
        memory = build_bounded_memory((SOURCE,), (c,), "2024-12-31")
        q = Question("q", "Ordinary text", "2024-01-02", "2024-12-31")
        self.assertEqual(memory.answer_key(q, (" ORG ", "ceo", "interim")).status, "answered")


class ProvenanceTests(unittest.TestCase):
    def test_future_source_and_context_excluded(self):
        future = Source("future", QUOTE, "2025-01-01")
        claims = [claim(state_observed_at=("2024-01-02",)), claim(claim_id="c2", source_id="future"), claim(claim_id="c3", context_source_ids=("future",))]
        memory = build_bounded_memory((SOURCE, future), claims, "2024-12-31")
        self.assertEqual([c.claim_id for c in memory.claims], ["c1"])
        self.assertEqual(memory.diagnostics["excluded_future_source_or_context_claim_ids"], ["c2", "c3"])
        self.assertEqual([s.source_id for s in memory.sources], ["s1"])

    def test_citations_retain_declared_context_dependencies(self):
        context = Source("context", "Context for the bound or identity.", "2024-01-01")
        c = claim(state_observed_at=("2024-01-02",), context_source_ids=("context",))
        result = answer([c], sources=(SOURCE, context))
        self.assertEqual(result.evidence_source_ids, ("context", "s1"))
        self.assertEqual(result.diagnostics["primary_evidence_source_ids"], ["s1"])
        self.assertEqual(result.diagnostics["context_dependency_source_ids"], ["context"])

    def test_report_date_is_not_state_observation(self):
        c = claim(reported_at="2024-01-02")
        self.assertFalse(claim_membership(c, "2024-01-02").must)

    def test_future_report_cannot_authorize_future_actual_answer_from_early_prefix(self):
        source = Source("s1", QUOTE, "2024-01-01")
        c = claim(reported_at="2024-01-10", state_observed_at=("2024-01-10",))
        with self.assertRaisesRegex(ValueError, "report date cannot follow"):
            build_bounded_memory((source,), (c,), "2024-01-02")

    def test_future_report_metadata_rejected_for_planned_claim_too(self):
        source = Source("s1", QUOTE, "2024-01-01")
        c = claim(modality="announced_future", reported_at="2024-01-10", start=DayBounds("2024-02-01", "2024-02-01"))
        with self.assertRaisesRegex(ValueError, "report date cannot follow"):
            build_bounded_memory((source,), (c,), "2024-01-02")

    def test_actual_observation_after_report_rejected(self):
        with self.assertRaisesRegex(ValueError, "follow its report"):
            claim(reported_at="2024-01-01", state_observed_at=("2024-01-02",))

    def test_actual_observation_after_availability_without_report_rejected(self):
        source = Source("s1", QUOTE, "2024-01-01")
        with self.assertRaisesRegex(ValueError, "follow source availability"):
            build_bounded_memory((source,), (claim(state_observed_at=("2024-01-02",)),), "2024-12-31")

    def test_cutoff_mismatch_rejected(self):
        memory = build_bounded_memory((SOURCE,), (claim(),), "2024-12-31")
        with self.assertRaises(ValueError):
            memory.answer_key(Question("q", "text", "2024-01-02", "2024-06-01"), ("org", "ceo", ""))

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate source"):
            build_bounded_memory((SOURCE, SOURCE), (), "2024-12-31")
        with self.assertRaisesRegex(ValueError, "Duplicate claim"):
            build_bounded_memory((SOURCE,), (claim(), claim()), "2024-12-31")

    def test_dangling_ids_rejected_even_future(self):
        future = Source("future", QUOTE, "2025-01-01")
        for c in (claim(source_id="missing"), claim(source_id="future", context_source_ids=("missing",))):
            with self.assertRaisesRegex(ValueError, "Dangling"):
                build_bounded_memory((SOURCE, future), (c,), "2024-12-31")

    def test_nonmatching_exact_span_rejected(self):
        for span in (EvidenceSpan(1, len(QUOTE) + 1, QUOTE), EvidenceSpan(0, 4, "xxxx")):
            with self.assertRaisesRegex(ValueError, "Nonmatching"):
                build_bounded_memory((SOURCE,), (claim(evidence_spans=(span,)),), "2024-12-31")

    def test_ungrounded_claim_and_invalid_span_rejected(self):
        with self.assertRaisesRegex(ValueError, "exact evidence"):
            claim(evidence_spans=())
        for args in ((True, 4, "abc"), (0, 0, ""), (-1, 2, "abc"), (0, 2, "abc")):
            with self.assertRaises(ValueError):
                EvidenceSpan(*args)

    def test_correction_explicitly_refused(self):
        with self.assertRaisesRegex(ValueError, "correction-aware"):
            claim(operation="CORRECTS")

    def test_unknown_serialized_field_rejected(self):
        record = claim_to_dict(claim())
        record["gold_key"] = "leak"
        with self.assertRaises(TypeError):
            claim_from_dict(record)

    def test_invalid_mode_rejected(self):
        with self.assertRaisesRegex(ValueError, "query mode"):
            answer([claim()], mode="latest")


if __name__ == "__main__":
    unittest.main()

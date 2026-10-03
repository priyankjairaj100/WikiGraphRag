"""Authored candidate-level contracts; these are not benchmark examples."""
from dataclasses import FrozenInstanceError, replace
import unittest

from temporal_state.decoder import (
    Link, Limits, Mention, Penalty, Problem, Reading, SearchLimitExceeded,
    decode_independent, decode_iterative, decode_joint, evaluate_assignment,
)
from temporal_state.models import Source


K = ("orion", "ceo", "parent")
OTHER = ("orion", "ceo", "subsidiary")


def unresolved():
    return Reading("unknown", None, None, -100.0)


def source(sid, date="2024-01-01"):
    return Source(sid, "Earlier report refers to the appointment. " + sid, date)


def mention(mid, readings, **kwargs):
    return Mention(mid, kwargs.pop("source_id", mid), 0, 14,
                   tuple(readings) + (unresolved(),), **kwargs)


def problem(mentions, links=(), sources=None, **kwargs):
    return Problem("2024-12-31", tuple(sources or [source(m.source_id) for m in mentions]),
                   tuple(mentions), tuple(links), "hand_authored_synthetic_logits", **kwargs)


def fixed(p, link_ids):
    return evaluate_assignment(p, {m.mention_id: m.readings[0].reading_id for m in p.mentions},
                               {m.mention_id: link_ids.get(m.mention_id) for m in p.mentions})


def trap_problem():
    a = mention("a", [Reading("parent", K, "Alice", 1), Reading("subsidiary", OTHER, "Alice", 0)])
    b = mention("b", [Reading("parent", K, "Bob", 1), Reading("subsidiary", OTHER, "Bob", 0)])
    link = Link("predecessor", "b", "subsidiary", "a", "subsidiary", "CHANGES", 3)
    return problem([a, b], [link])


class DecoderTests(unittest.TestCase):
    def test_joint_escapes_a_single_coordinate_local_optimum(self):
        p = trap_problem()
        independent, iterative, joint = decode_independent(p), decode_iterative(p), decode_joint(p)
        self.assertEqual((independent.objective, iterative.objective, joint.objective), (2, 2, 3))
        self.assertEqual(dict(joint.reading_ids), {"a": "subsidiary", "b": "subsidiary"})
        self.assertTrue(iterative.diagnostics.coordinate_converged)
        self.assertFalse(iterative.diagnostics.global_optimal)
        self.assertTrue(joint.diagnostics.global_optimal)
        for result in (independent, iterative, joint):
            self.assertEqual(evaluate_assignment(p, dict(result.reading_ids), dict(result.link_ids)),
                             result.objective)

    def test_seeded_restarts_can_repair_the_authored_trap(self):
        p = trap_problem()
        result = decode_iterative(p, restarts=10, seed=7)
        self.assertEqual(result.objective, decode_joint(p).objective)
        self.assertEqual(len(result.diagnostics.restart_objectives), 11)
        self.assertFalse(result.diagnostics.global_optimal)
        self.assertEqual(result.reading_ids, decode_iterative(p, restarts=10, seed=7).reading_ids)

    def test_easy_shared_features_example_gives_no_joint_benefit(self):
        p = trap_problem()
        p = replace(p, links=(Link("clear", "b", "parent", "a", "parent", "CHANGES", 3),))
        results = [fn(p) for fn in (decode_independent, decode_iterative, decode_joint)]
        self.assertEqual([r.objective for r in results], [5, 5, 5])
        self.assertEqual(len({r.reading_ids for r in results}), 1)

    def test_iterative_repairs_a_single_reading_when_it_improves_objective(self):
        a = mention("a", [Reading("p", K, "Alice", 1)])
        b = mention("b", [Reading("p", K, "Bob", 0), Reading("s", OTHER, "Bob", 1)])
        p = problem([a, b], [Link("l", "b", "p", "a", "p", "CHANGES", 3)])
        self.assertEqual(decode_independent(p).objective, 2)
        self.assertEqual(decode_iterative(p).objective, 4)
        self.assertEqual(decode_iterative(p).objective, decode_joint(p).objective)
        limited = decode_iterative(p, replace(Limits(), max_sweeps=1))
        self.assertEqual(limited.objective, 4)
        self.assertEqual(limited.diagnostics.stop_reason, "sweep_limit")
        self.assertFalse(limited.diagnostics.coordinate_converged)

    def test_reversion_has_three_components_and_does_not_merge_equal_values(self):
        readings = [("a", "Alice", "2020-01-01"), ("b", "Bob", "2021-01-01"),
                    ("c", "Bob", "2021-01-01"), ("d", "Alice", "2023-01-01")]
        mentions = [mention(mid, [Reading("r", K, value, 1, effective_start=start)])
                    for mid, value, start in readings]
        links = [Link("b-a", "b", "r", "a", "r", "CHANGES", 5),
                 Link("c-b", "c", "r", "b", "r", "RESTATES", 5),
                 Link("d-c", "d", "r", "c", "r", "CHANGES", 5)]
        result = decode_joint(problem(mentions, links))
        self.assertEqual(result.restatement_components, (("a",), ("b", "c"), ("d",)))
        self.assertEqual(result.objective, 19)

    def test_restatement_equality_cannot_collapse_a_change_path(self):
        mentions = [mention("a", [Reading("r", K, "Alice", 1)]),
                    mention("b", [Reading("r", K, "Bob", 1)]),
                    mention("c", [Reading("r", K, "Alice", 1)])]
        links = [Link("a-c", "a", "r", "c", "r", "RESTATES", 2),
                 Link("b-a", "b", "r", "a", "r", "CHANGES", 2),
                 Link("c-b", "c", "r", "b", "r", "CHANGES", 2)]
        p = problem(mentions, links)
        self.assertIsNone(fixed(p, {"a": "a-c", "b": "b-a", "c": "c-b"}))
        self.assertEqual(decode_joint(p).objective, 7)  # At most two of three edges.

    def test_pure_restatement_cycle_is_equality_and_is_permitted(self):
        mentions = [mention(x, [Reading("r", K, "Alice", 1)]) for x in ("a", "b")]
        links = [Link("a-b", "a", "r", "b", "r", "RESTATES", 1),
                 Link("b-a", "b", "r", "a", "r", "RESTATES", 1)]
        self.assertEqual(fixed(problem(mentions, links), {"a": "a-b", "b": "b-a"}), 4)

    def test_correction_cycle_is_infeasible_even_with_same_day_references(self):
        mentions = [mention(x, [Reading("r", K, x, 1)]) for x in ("a", "b")]
        links = [Link("a-b", "a", "r", "b", "r", "CORRECTS", 5, reference_span=(0, 14)),
                 Link("b-a", "b", "r", "a", "r", "CORRECTS", 5, reference_span=(0, 14))]
        p = problem(mentions, links)
        self.assertIsNone(fixed(p, {"a": "a-b", "b": "b-a"}))
        self.assertEqual(decode_joint(p).objective, 7)

    def test_same_day_correction_requires_explicit_reference(self):
        mentions = [mention(x, [Reading("r", K, x, 1)]) for x in ("a", "b")]
        link = Link("fix", "b", "r", "a", "r", "CORRECTS", 5)
        p = problem(mentions, [link])
        self.assertIsNone(fixed(p, {"b": "fix"}))
        p = replace(p, links=(replace(link, reference_span=(0, 14)),))
        self.assertEqual(fixed(p, {"b": "fix"}), 7)
        with self.assertRaises(ValueError):
            decode_joint(replace(p, links=(replace(link, reference_span=(0, 999)),)))

    def test_correction_cannot_point_to_a_later_available_source(self):
        mentions = [mention(x, [Reading("r", K, x, 1)]) for x in ("a", "b")]
        link = Link("fix", "a", "r", "b", "r", "CORRECTS", 5, reference_span=(0, 14))
        p = problem(mentions, [link], [source("a", "2024-01-01"), source("b", "2024-01-02")])
        self.assertIsNone(fixed(p, {"a": "fix"}))
        self.assertEqual(dict(decode_joint(p).link_ids)["a"], None)

    def test_correction_can_revise_dates_without_being_a_change(self):
        a = mention("a", [Reading("r", K, "Alice", 1, effective_start="2022-01-01")])
        b = mention("b", [Reading("r", K, "Alice", 1, effective_start="2021-01-01")])
        link = Link("fix", "b", "r", "a", "r", "CORRECTS", 5)
        p = problem([a, b], [link], [source("a", "2024-01-01"), source("b", "2024-02-01")])
        self.assertEqual(fixed(p, {"b": "fix"}), 7)
        self.assertEqual(decode_joint(p).restatement_components, (("a",), ("b",)))

    def test_missing_target_is_malformed_and_unresolved_null_remains_available(self):
        m = mention("a", [Reading("r", K, "Alice", 0)])
        p = problem([m], [Link("fix", "a", "r", "missing", "r", "CORRECTS", 3)])
        with self.assertRaises(ValueError):
            decode_joint(p)
        m = replace(m, readings=(replace(unresolved(), unary_score=1), m.readings[0]))
        result = decode_joint(problem([m]))
        self.assertEqual(result.reading_ids, (("a", "unknown"),))
        self.assertEqual(result.link_ids, (("a", None),))
        self.assertEqual(result.restatement_components, ())

    def test_scope_mismatch_does_not_allow_a_correction_or_change(self):
        a = mention("a", [Reading("r", K, "Alice", 1)])
        b = mention("b", [Reading("r", OTHER, "Bob", 1)])
        for relation in ("CORRECTS", "CHANGES", "RESTATES"):
            link = Link("l", "b", "r", "a", "r", relation, 10, reference_span=(0, 14))
            self.assertIsNone(fixed(problem([a, b], [link]), {"b": "l"}))

    def test_exact_interval_semantics_and_unknown_intermediates(self):
        mentions = [mention("a", [Reading("r", K, "Alice", 1, effective_start="2023-01-01")]),
                    mention("b", [Reading("r", K, "Bob", 1)]),
                    mention("c", [Reading("r", K, "Carol", 1, effective_start="2021-01-01")])]
        links = [Link("b-a", "b", "r", "a", "r", "CHANGES", 1),
                 Link("c-b", "c", "r", "b", "r", "CHANGES", 1)]
        self.assertIsNone(fixed(problem(mentions, links), {"b": "b-a", "c": "c-b"}))

    def test_component_date_consistency_is_transitive(self):
        a = mention("a", [Reading("r", K, "Alice", 1)])
        b = mention("b", [Reading("r", K, "Alice", 1, effective_start="2020-01-01")])
        c = mention("c", [Reading("r", K, "Alice", 1, effective_start="2021-01-01")])
        links = [Link("b-a", "b", "r", "a", "r", "RESTATES", 1),
                 Link("c-a", "c", "r", "a", "r", "RESTATES", 1)]
        self.assertIsNone(fixed(problem([a, b, c], links), {"b": "b-a", "c": "c-a"}))

    def test_overlapping_disagreement_is_retained_with_null_links(self):
        a = mention("a", [Reading("r", K, "Alice", 1, "2020-01-01", "2023-01-01")])
        b = mention("b", [Reading("r", K, "Bob", 1, "2021-01-01", "2024-01-01")])
        link = Link("l", "b", "r", "a", "r", "CHANGES", 5)
        p = problem([a, b], [link])
        self.assertIsNone(fixed(p, {"b": "l"}))
        self.assertEqual(fixed(p, {}), 2)
        result = decode_joint(p)
        self.assertEqual(dict(result.reading_ids), {"a": "r", "b": "r"})
        self.assertTrue(all(lid is None for _, lid in result.link_ids))

    def test_observation_date_is_not_promoted_to_effective_start(self):
        a = mention("a", [Reading("r", K, "Alice", 1, observed_at="2020-01-01")])
        b = mention("b", [Reading("r", K, "Bob", 1, observed_at="2022-01-01")])
        p = problem([a, b], [Link("l", "b", "r", "a", "r", "CHANGES", 1)])
        self.assertEqual(fixed(p, {"b": "l"}), 3)
        self.assertIsNone(p.mentions[1].readings[0].effective_start)
        reversed_b = replace(b, readings=(replace(b.readings[0], observed_at="2019-01-01"), unresolved()))
        self.assertIsNone(fixed(replace(p, mentions=(a, reversed_b)), {"b": "l"}))

    def test_prospective_and_retrospective_effective_dates_ignore_report_order(self):
        a = mention("a", [Reading("r", K, "Alice", 1, effective_start="2020-01-01")])
        b = mention("b", [Reading("r", K, "Bob", 1, effective_start="2025-01-01")])
        sources = [source("a", "2024-06-01"), source("b", "2024-01-01")]
        p = problem([a, b], [Link("l", "b", "r", "a", "r", "CHANGES", 1)], sources)
        self.assertEqual(fixed(p, {"b": "l"}), 3)

    def test_future_sources_and_declared_derived_context_are_rejected(self):
        p = trap_problem()
        future = source("future", "2025-01-01")
        with self.assertRaisesRegex(ValueError, "available in this prefix"):
            decode_joint(replace(p, sources=p.sources + (future,)))
        for changed in (replace(p, context_source_ids=("future",)),
                        replace(p, mentions=(replace(p.mentions[0], context_source_ids=("future",)), p.mentions[1])),
                        replace(p, links=(replace(p.links[0], context_source_ids=("future",)),))):
            with self.assertRaisesRegex(ValueError, "eligible prefix"):
                decode_joint(changed)
        r = replace(p.mentions[0].readings[0], context_source_ids=("future",))
        m = replace(p.mentions[0], readings=(r,) + p.mentions[0].readings[1:])
        with self.assertRaisesRegex(ValueError, "eligible prefix"):
            decode_joint(replace(p, mentions=(m, p.mentions[1])))

    def test_shuffled_prefix_and_candidates_have_identical_decisions(self):
        p = trap_problem()
        q = replace(p, sources=p.sources[::-1], mentions=tuple(
            replace(m, readings=m.readings[::-1]) for m in p.mentions[::-1]), links=p.links[::-1])
        for method in (decode_joint, decode_independent, decode_iterative):
            a, b = method(p), method(q)
            self.assertEqual((a.objective, a.reading_ids, a.link_ids, a.diagnostics.prefix_digest),
                             (b.objective, b.reading_ids, b.link_ids, b.diagnostics.prefix_digest))
        changed_source = replace(p.sources[0], text=p.sources[0].text + " new version")
        self.assertNotEqual(decode_joint(p).diagnostics.prefix_digest,
                            decode_joint(replace(p, sources=(changed_source,) + p.sources[1:])).diagnostics.prefix_digest)

    def test_same_named_soft_penalties_and_null_intercept_apply_to_all_methods(self):
        p = trap_problem()
        p = replace(p, links=(replace(p.links[0], violations=("unsupported_scope",)),),
                    penalties=(Penalty("unsupported_scope", 4),))
        for method in (decode_joint, decode_independent, decode_iterative):
            self.assertEqual(method(p).objective, 2)
        p = replace(p, mentions=tuple(replace(m, null_score=2) for m in p.mentions))
        for method in (decode_joint, decode_independent, decode_iterative):
            self.assertEqual(method(p).objective, 6)

    def test_explicit_enumeration_ceiling_and_actual_counts(self):
        p = trap_problem()
        joint = decode_joint(p)
        self.assertEqual(joint.diagnostics.state_ceiling, 18)
        self.assertEqual(joint.diagnostics.reading_assignments_evaluated, 9)
        self.assertLessEqual(joint.diagnostics.link_assignments_evaluated, 18)
        for method in (decode_joint, decode_independent, decode_iterative):
            with self.assertRaises(SearchLimitExceeded):
                method(p, replace(Limits(), max_states=17))

    def test_malformed_intervals_scores_ids_and_mutable_inputs_are_rejected(self):
        p = trap_problem()
        bad_readings = [replace(p.mentions[0].readings[0], effective_start="2020-01-01", effective_end="2020-01-01"),
                        replace(p.mentions[0].readings[0], unary_score=float("nan")),
                        replace(p.mentions[0].readings[0], effective_start="2020-01-01", observed_at="2019-01-01"),
                        replace(p.mentions[0].readings[0], violations=("undeclared",))]
        for r in bad_readings:
            with self.assertRaises(ValueError):
                decode_joint(replace(p, mentions=(replace(p.mentions[0], readings=(r, unresolved())), p.mentions[1])))
        with self.assertRaises(ValueError):
            decode_joint(replace(p, mentions=list(p.mentions)))
        with self.assertRaises(ValueError):
            decode_joint(replace(p, mentions=(p.mentions[0], p.mentions[0])))
        with self.assertRaises(ValueError):
            decode_joint(replace(p, links=(replace(p.links[0], relation="guessed"),)))
        with self.assertRaises(FrozenInstanceError):
            p.beta = 2

    def test_required_unresolved_reading_and_empty_problem(self):
        p = trap_problem()
        with self.assertRaisesRegex(ValueError, "unresolved"):
            decode_joint(replace(p, mentions=(replace(p.mentions[0], readings=p.mentions[0].readings[:2]),)))
        empty = Problem("2024-01-01", (), (), (), "synthetic")
        for method in (decode_joint, decode_independent, decode_iterative):
            result = method(empty)
            self.assertEqual(result.objective, 0)
            self.assertEqual(result.reading_ids, ())


if __name__ == "__main__":
    unittest.main()

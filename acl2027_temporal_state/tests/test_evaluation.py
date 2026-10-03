"""Tests for scoring mistakes that would otherwise inflate reported quality."""
import unittest

from temporal_state.evaluation import (
    GoldAnswer, evaluate_prediction, grouped_bootstrap, grouped_paired_bootstrap,
)
from temporal_state.models import Assertion, Prediction, Question, Source


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.question = Question("q", "Who leads Acme?", "2021-06-01", "2022-01-01")
        self.sources = [
            Source("current", "B is leader.", "2021-01-01"),
            Source("stale", "A was leader.", "2020-01-01"),
            Source("corroboration", "B leads Acme.", "2021-06-01"),
            Source("future", "C later corrected B.", "2023-01-01"),
        ]
        self.gold = GoldAnswer("q", "h", "answered", (("B",),), (("current",),))

    def score(self, **kwargs):
        return evaluate_prediction(self.question, Prediction("q", **kwargs), self.gold, self.sources)

    def test_whole_value_case_whitespace_match(self):
        result = self.score(status="answered", values=("  b  ",), evidence_source_ids=("current",))
        self.assertTrue(result.correct)

    def test_current_and_stale_both_named_fails(self):
        result = self.score(status="answered", values=("A", "B"), evidence_source_ids=("current",))
        self.assertFalse(result.correct)
        self.assertFalse(result.answer_correct)

    def test_containment_is_not_exact_match(self):
        result = self.score(status="answered", values=("B or C",), evidence_source_ids=("current",))
        self.assertFalse(result.answer_correct)

    def test_partial_name_or_numeric_substring_never_matches(self):
        for expected, actual in [("Tim Cook", "Tim Allen"), ("15", "150"), ("15", "amount 15")]:
            gold = GoldAnswer("q", "h", "answered", ((expected,),), (("current",),))
            result = evaluate_prediction(self.question,
                Prediction("q", "answered", (actual,), ("current",)), gold, self.sources)
            self.assertFalse(result.answer_correct)

    def test_answerable_abstention_wrong_even_with_evidence(self):
        result = self.score(status="abstain", evidence_source_ids=("current",))
        self.assertFalse(result.correct)
        self.assertTrue(result.abstained)

    def test_complete_coholder_value_set_required(self):
        gold = GoldAnswer("q", "h", "answered", (("A", "B"),), (("current", "stale"),))
        for values, expected in [(("B", "A"), True), (("A",), False)]:
            result = evaluate_prediction(self.question,
                Prediction("q", "answered", values, ("current", "stale")), gold, self.sources)
            self.assertEqual(result.correct, expected)

    def test_alternative_sufficient_evidence_sets(self):
        gold = GoldAnswer("q", "h", "answered", (("B",),), (("current",), ("corroboration",)))
        result = evaluate_prediction(self.question,
            Prediction("q", "answered", ("B",), ("corroboration",)), gold, self.sources)
        self.assertTrue(result.correct)

    def test_partial_chain_is_not_complete_support(self):
        gold = GoldAnswer("q", "h", "answered", (("B",),), (("current", "corroboration"),))
        result = evaluate_prediction(self.question,
            Prediction("q", "answered", ("B",), ("current",)), gold, self.sources)
        self.assertFalse(result.correct)

    def test_missing_source_rejects_prediction_grounding(self):
        result = self.score(status="answered", values=("B",), evidence_source_ids=("current", "missing"))
        self.assertFalse(result.correct)
        self.assertEqual(result.unknown_source_ids, ("missing",))

    def test_future_extra_source_invalidates_even_correct_answer(self):
        result = self.score(status="answered", values=("B",), evidence_source_ids=("current", "future"))
        self.assertFalse(result.correct)
        self.assertFalse(result.source_cutoff_valid)

    def test_cutoff_inclusive(self):
        question = Question("q", "Who leads Acme?", "2021-01-01", "2021-01-01")
        result = evaluate_prediction(question, Prediction("q", "answered", ("B",), ("current",)),
                                     self.gold, self.sources)
        self.assertTrue(result.correct)

    def test_indeterminate_requires_support_not_bare_refusal(self):
        gold = GoldAnswer("q", "h", "indeterminate", (), (("current", "stale"),))
        for status, sources, expected in [
            ("indeterminate", ("current", "stale"), True),
            ("indeterminate", ("current",), False),
            ("abstain", ("current", "stale"), False),
        ]:
            result = evaluate_prediction(self.question, Prediction("q", status, (), sources), gold, self.sources)
            self.assertEqual(result.correct, expected)

    def test_indeterminate_does_not_assert_competing_values(self):
        gold = GoldAnswer("q", "h", "indeterminate", (), (("current", "stale"),))
        result = evaluate_prediction(self.question,
            Prediction("q", "indeterminate", ("A", "B"), ("current", "stale")), gold, self.sources)
        self.assertFalse(result.correct)

    def test_bad_gold_future_source_is_dataset_error(self):
        gold = GoldAnswer("q", "h", "answered", (("B",),), (("future",),))
        with self.assertRaisesRegex(ValueError, "Gold uses future"):
            evaluate_prediction(self.question, Prediction("q", "abstain"), gold, self.sources)

    def test_gold_requires_independent_group_and_support(self):
        with self.assertRaises(ValueError):
            GoldAnswer("q", "", "answered", (("B",),), (("current",),))
        with self.assertRaises(ValueError):
            GoldAnswer("q", "h", "indeterminate")

    def test_mismatched_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "IDs must match"):
            evaluate_prediction(self.question, Prediction("other", "abstain"), self.gold, self.sources)

    def test_changing_gold_cannot_change_inference(self):
        from temporal_state.memory import build_memory
        sources = [Source("claim", "Ben is CEO of Acme since 2020.", "2020-01-01")]
        assertions = [Assertion("a", "claim", "Acme", "CEO", "Ben", "2020-01-01")]
        question = Question("q", "Who is the CEO of Acme?", "2021-01-01", "2021-01-01")
        memory = build_memory(sources, assertions, question.information_cutoff)
        before = memory.answer(question)
        good = GoldAnswer("q", "h", "answered", (("Ben",),), (("claim",),))
        changed = GoldAnswer("q", "h", "answered", (("Invented Gold",),), (("claim",),))
        self.assertTrue(evaluate_prediction(question, before, good, sources).correct)
        self.assertFalse(evaluate_prediction(question, before, changed, sources).correct)
        self.assertEqual(memory.answer(question), before)


class HistoryBootstrapTests(unittest.TestCase):
    def test_equal_history_weight_not_question_weight(self):
        result = grouped_bootstrap(["long"] * 9 + ["short"], [1.] * 9 + [0.], n_resamples=100, seed=3)
        self.assertEqual(result["mean"], 0.5)
        self.assertEqual(result["n_histories"], 2)
        self.assertEqual(result["n_items"], 10)

    def test_deterministic_and_order_independent(self):
        a = grouped_bootstrap(["a", "b", "a"], [1., 0., 0.], n_resamples=200, seed=4)
        b = grouped_bootstrap(["b", "a", "a"], [0., 0., 1.], n_resamples=200, seed=4)
        self.assertEqual(a, b)

    def test_one_history_cannot_create_independent_probes(self):
        result = grouped_bootstrap(["a", "a"], [1., 0.], n_resamples=20)
        self.assertEqual(result["n_histories"], 1)
        self.assertEqual(result["ci95_low"], 0.5)
        self.assertEqual(result["ci95_high"], 0.5)

    def test_paired_groups_and_no_p_value(self):
        result = grouped_paired_bootstrap(["a", "a", "b"], [1., 1., 0.], [0., 0., 1.], n_resamples=100)
        self.assertEqual(result["mean"], 0.)
        self.assertNotIn("p_value", result)

    def test_reject_empty_invalid_and_unaligned_inputs(self):
        for histories, scores in [([], []), (["a"], []), ([""], [1.]), (["a"], [float("nan")]), (["a"], [2.])]:
            with self.assertRaises(ValueError):
                grouped_bootstrap(histories, scores)
        with self.assertRaises(ValueError):
            grouped_paired_bootstrap(["a"], [1.], [])


if __name__ == "__main__":
    unittest.main()

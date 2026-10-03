"""Authored contract challenges, not natural-source QA or algorithmic gains."""
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
import unittest

from temporal_state.witness_selection_v14 import (
    Bundle, Cost, Hypothesis, Judgment, Problem, Source, SourceJudgment, Span,
    evaluate, exhaustive_tiny_optimum, greedy_select, render, union_spans,
)


UNIT = 'authored_character_fixture'


def counter(text):
    # Deliberately NOT a tokenizer. It charges all fixture source text/labels.
    return Cost(len(text), UNIT, 'Authored test counter; not native reader tokens')


def source(sid='authored:p1', text='VALUE\nCAPTION\nNOTE\nUNRELATED\n'):
    return Source(sid, text, sha256(text.encode()).hexdigest(), 'Authored fixture page')


VALUE = Span('authored:p1', 0, 5)
CAPTION = Span('authored:p1', 6, 13)
NOTE = Span('authored:p1', 14, 18)
UNRELATED = Span('authored:p1', 19, 28)


def judgment(jid, hypothesis, claim, verdict, spans):
    return SourceJudgment(jid, hypothesis, claim, verdict, spans,
                          'Authored oracle separator/support map; no automatic verifier')


def fixture(*, joint=True, unknown=False):
    bundles = [Bundle('value', (VALUE,))]
    bundles += ([Bundle('joint', (CAPTION, NOTE))] if joint else
                [Bundle('caption', (CAPTION,)), Bundle('note', (NOTE,))])
    return Problem(
        sources=(source(),),
        hypotheses=(Hypothesis('a', ('value', 'scope'), Fraction(2)),
                    Hypothesis('b', ('value', 'scope'), Fraction(3))),
        bundles=tuple(bundles),
        judgments=(judgment('a_value', 'a', 'value', Judgment.SUPPORTED, (VALUE,)),
                   judgment('b_value', 'b', 'value', Judgment.SUPPORTED, (VALUE,)),
                   judgment('a_scope', 'a', 'scope', Judgment.SUPPORTED, (CAPTION, NOTE)),
                   judgment('b_scope', 'b', 'scope', Judgment.CONTRADICTED, (CAPTION, NOTE))),
        incompatible_pairs=frozenset({('a', 'b')}),
        input_provenance='Authored oracle hypotheses, incompatibilities and judgments',
        unknown_alternatives_remain=unknown,
        unknown_assessment_provenance='Finite authored fixture only, not natural coverage',
        prompt_prefix='Authored question and reader instructions',
    )


def state(problem, selected=(), budget=10000):
    return evaluate(problem, tuple(selected), counter, budget, UNIT)


class WitnessSemantics(unittest.TestCase):
    def test_fixed_weight_pair_loss_does_not_renormalize_survivors(self):
        p = replace(fixture(), hypotheses=tuple(Hypothesis(h, ('scope',), Fraction(w))
                    for h, w in [('a', 2), ('b', 3), ('c', 5)]),
                    bundles=(Bundle('separator', (CAPTION,)),),
                    judgments=(judgment('not_a', 'a', 'scope', Judgment.CONTRADICTED, (CAPTION,)),),
                    incompatible_pairs=frozenset({('a', 'b'), ('a', 'c'), ('b', 'c')}))
        self.assertEqual(state(p).loss, Fraction(31))
        result = state(p, ['separator'])
        self.assertEqual(result.survivors, ('b', 'c'))
        self.assertEqual(result.loss, Fraction(15))

    def test_missing_and_explicit_unresolved_are_not_contradictions(self):
        p = replace(fixture(), bundles=(Bundle('unrelated', (UNRELATED,)),),
                    judgments=(judgment('unknown', 'b', 'scope', Judgment.UNRESOLVED, (UNRELATED,)),))
        result = state(p, ['unrelated'])
        self.assertEqual(result.survivors, ('a', 'b'))
        self.assertEqual(result.loss, Fraction(6))
        self.assertTrue(all(x[2] is Judgment.UNRESOLVED for x in result.claim_states))

    def test_joint_judgment_needs_both_received_spans(self):
        p = fixture(joint=False)
        for selected in [(), ('caption',), ('note',)]:
            result = state(p, selected)
            self.assertEqual(result.survivors, ('a', 'b'))
            self.assertNotIn('b_scope', result.activated_judgment_ids)
        self.assertEqual(state(p, ['caption', 'note']).survivors, ('a',))

    def test_indivisible_bundle_cannot_be_partially_packed_to_fit(self):
        p = replace(fixture(), bundles=(Bundle('joint', (CAPTION, NOTE)),))
        budget = state(p, ['joint']).cost.amount - 1
        too_large = state(p, ['joint'], budget)
        self.assertIn('over_budget', too_large.blockers)
        chosen = greedy_select(p, counter, budget, UNIT)
        self.assertEqual(chosen.selected_bundle_ids, ())
        self.assertEqual(chosen.received_spans, ())
        self.assertFalse(chosen.answerable)

    def test_source_coverage_activates_judgment_independent_of_bundle_names(self):
        p = replace(fixture(), bundles=(Bundle('renamed_whole_source', (Span('authored:p1', 0, 18),)),))
        result = state(p, ['renamed_whole_source'])
        self.assertEqual(result.survivors, ('a',))
        self.assertTrue(result.answerable)

    def test_conflicting_judgments_cannot_silently_eliminate_alternative(self):
        p = fixture()
        p = replace(p, judgments=p.judgments + (
            judgment('disputed_b', 'b', 'scope', Judgment.SUPPORTED, (CAPTION, NOTE)),))
        result = state(p, ['value', 'joint'])
        self.assertIn('b', result.survivors)
        self.assertIn(('b', 'scope'), result.conflicts)
        self.assertFalse(result.admissible)
        self.assertFalse(result.answerable)

    def test_eliminating_every_hypothesis_is_not_optimal_success(self):
        p = replace(fixture(), bundles=(Bundle('annihilate', (CAPTION,)),),
                    judgments=tuple(judgment('not_'+h, h, 'scope', Judgment.CONTRADICTED, (CAPTION,))
                                    for h in ('a', 'b')))
        result = state(p, ['annihilate'])
        self.assertEqual(result.loss, 0)
        self.assertFalse(result.admissible)
        self.assertIn('no_surviving_hypothesis', result.blockers)
        self.assertEqual(greedy_select(p, counter, 10000, UNIT).selected_bundle_ids, ())
        self.assertEqual(exhaustive_tiny_optimum(p, counter, 10000, UNIT).selected_bundle_ids, ())

    def test_zero_loss_is_not_positive_support_or_unknown_clearance(self):
        p = fixture()
        unsupported = state(p, ['joint'])
        self.assertEqual(unsupported.loss, 0)
        self.assertFalse(unsupported.answerable)
        self.assertIn('missing_positive_support', unsupported.blockers)
        complete = state(p, ['joint', 'value'])
        self.assertTrue(complete.answerable)
        unknown = state(replace(p, unknown_alternatives_remain=True), ['joint', 'value'])
        self.assertEqual(unknown.loss, 0)
        self.assertIn('unmodeled_alternatives_unresolved', unknown.blockers)
        self.assertFalse(unknown.answerable)

    def test_zero_weight_incompatible_alternative_still_blocks_answer(self):
        p = replace(fixture(), hypotheses=(Hypothesis('a', ('value',), Fraction(1)),
                    Hypothesis('b', ('value',), Fraction(0))),
                    judgments=tuple(judgment('value_'+h, h, 'value', Judgment.SUPPORTED, (VALUE,))
                                    for h in ('a', 'b')))
        result = state(p, ['value'])
        self.assertEqual(result.loss, 0)
        self.assertEqual(result.supported_hypotheses, ('a', 'b'))
        self.assertIn('incompatible_survivors', result.blockers)
        self.assertFalse(result.answerable)


class CostAndSearchContracts(unittest.TestCase):
    def test_union_counts_overlap_once_preserves_gaps_and_source_identity(self):
        spans = (Span('a', 0, 4), Span('a', 2, 6), Span('a', 0, 4),
                 Span('a', 8, 10), Span('b', 0, 4))
        self.assertEqual(union_spans(spans), (Span('a', 0, 6), Span('a', 8, 10), Span('b', 0, 4)))
        p = replace(fixture(), bundles=(Bundle('left', (Span('authored:p1', 0, 8),)),
                    Bundle('right', (Span('authored:p1', 6, 18),)),
                    Bundle('whole', (Span('authored:p1', 0, 18),))))
        left_right, whole = state(p, ['left', 'right']), state(p, ['whole'])
        self.assertEqual(left_right.received_spans, whole.received_spans)
        self.assertEqual(left_right.cost, whole.cost)
        self.assertGreater(whole.cost.amount, 18)  # Source labels/instructions are charged.
        self.assertEqual(whole.cost.amount, len(render(p, whole.received_spans)))

    def test_greedy_joint_bundle_succeeds_but_split_synergy_can_defeat_it(self):
        joint = greedy_select(fixture(), counter, 10000, UNIT)
        self.assertTrue(joint.answerable)
        p = fixture(joint=False)
        greedy = greedy_select(p, counter, 10000, UNIT)
        optimum = exhaustive_tiny_optimum(p, counter, 10000, UNIT, require_answerable=True)
        self.assertEqual(greedy.selected_bundle_ids, ('value',))
        self.assertEqual(greedy.loss, 6)
        self.assertFalse(greedy.answerable)
        self.assertIsNotNone(optimum)
        self.assertEqual(optimum.loss, 0)
        self.assertTrue(optimum.answerable)
        self.assertEqual(optimum.selected_bundle_ids, ('caption', 'note', 'value'))

    def test_exhaustive_answerable_constraint_and_budget_failure_are_explicit(self):
        p = fixture(unknown=True)
        self.assertIsNone(exhaustive_tiny_optimum(p, counter, 10000, UNIT, require_answerable=True))
        p = fixture()
        self.assertIsNone(exhaustive_tiny_optimum(p, counter, 0, UNIT))
        self.assertIn('over_budget', greedy_select(p, counter, 0, UNIT).blockers)
        with self.assertRaises(ValueError):
            exhaustive_tiny_optimum(p, counter, 10000, UNIT, max_bundles=1)

    def test_greedy_ties_are_input_order_independent(self):
        p = replace(fixture(), bundles=(Bundle('z', (Span('authored:p1', 0, 18),)),
                    Bundle('a', (Span('authored:p1', 0, 18),))))
        first = greedy_select(p, counter, 10000, UNIT)
        second = greedy_select(replace(p, bundles=tuple(reversed(p.bundles))), counter, 10000, UNIT)
        self.assertEqual(first, second)
        self.assertEqual(first.selected_bundle_ids, ('a',))

    def test_invalid_provenance_identity_anchors_and_costs_fail_loudly(self):
        with self.assertRaises(ValueError):
            replace(source(), text_sha256='0'*64)
        with self.assertRaises(ValueError):
            replace(fixture(), unknown_assessment_provenance='')
        with self.assertRaises(ValueError):
            replace(fixture(), bundles=(Bundle('bad', (Span('authored:p1', 0, 999),)),))
        with self.assertRaises(ValueError):
            replace(fixture(), incompatible_pairs=frozenset({('b', 'a')}))
        with self.assertRaises(ValueError):
            state(fixture(), ['missing'])
        with self.assertRaises(ValueError):
            evaluate(fixture(), (), counter, 10000, 'native_tokens')
        with self.assertRaises(ValueError):
            Hypothesis('vacuous', ())
        with self.assertRaises(ValueError):
            Cost(-1, UNIT, 'fixture')


if __name__ == '__main__':
    unittest.main()

"""The same finite objective extension is used by every search path."""
import math
import unittest

from temporal_state.decoder import (Mention, Problem, Reading, decode_independent,
    decode_iterative, decode_joint, evaluate_assignment)
from temporal_state.models import Source


class ObjectiveExtensionTests(unittest.TestCase):
    def setUp(self):
        self.problem = Problem('2024-01-02', (Source('s', 'A B', '2024-01-01'),),
            (Mention('m', 's', 0, 1, (
                Reading('a', ('x', 'r', ''), 'A', 3),
                Reading('b', ('x', 'r', ''), 'B', 2),
                Reading('u', None, None, 0))),), (), 'authored API control')

    def test_joint_and_coordinate_optimize_extension_but_independent_freezes_unary(self):
        seen = []
        def objective(selected, chosen, components):
            self.assertIsInstance(chosen, tuple)
            self.assertIsInstance(components, tuple)
            seen.append(selected['m'].reading_id)
            return 9.0 if selected['m'].reading_id == 'b' else 0.0
        independent = decode_independent(self.problem, objective_function=objective)
        self.assertEqual(independent.reading_ids, (('m', 'a'),))
        self.assertEqual(independent.objective, 0.0)
        for result in (decode_joint(self.problem, objective_function=objective),
                       decode_iterative(self.problem, objective_function=objective),
                       decode_iterative(self.problem, objective_function=objective, restarts=2)):
            self.assertEqual(result.reading_ids, (('m', 'b'),))
            self.assertEqual(result.objective, 9.0)
        self.assertEqual(evaluate_assignment(self.problem, {'m': 'b'}, {'m': None},
            objective_function=objective), 9.0)
        self.assertIn('u', seen)

    def test_invalid_objective_outputs_fail_closed(self):
        for invalid in (True, False, None, '1', math.inf, -math.inf, math.nan):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decode_joint(self.problem, objective_function=lambda *args: invalid)
        with self.assertRaises(ValueError):
            decode_joint(self.problem, objective_function=1)

    def test_objective_not_called_for_rejected_state(self):
        calls = []
        def semantics(selected, chosen):
            return None if selected['m'].reading_id == 'b' else ()
        def objective(*args):
            calls.append(args)
            return 1
        self.assertIsNone(evaluate_assignment(self.problem, {'m': 'b'}, {'m': None},
            state_semantics=semantics, objective_function=objective))
        self.assertEqual(calls, [])

    def test_default_preserves_original_objective(self):
        self.assertEqual(decode_joint(self.problem).objective, 3)
        self.assertEqual(evaluate_assignment(self.problem, {'m': 'a'}, {'m': None}), 3)


if __name__ == '__main__':
    unittest.main()

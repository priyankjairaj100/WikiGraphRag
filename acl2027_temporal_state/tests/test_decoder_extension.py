"""Fair local costs and a shared, link-only semantic extension."""
from dataclasses import replace
import unittest

from temporal_state.decoder import (
    Link, Mention, Penalty, Problem, Reading, decode_independent,
    decode_iterative, decode_joint, evaluate_assignment,
)
from temporal_state.models import Source


def example():
    source = Source('s', 'Synthetic source evidence.', '2024-01-01')
    key = ('example', 'ceo', '')
    mentions = tuple(Mention(mid, 's', 0, 9, (
        Reading('r', key, value, 2), Reading('unknown', None, None, -10)))
        for mid, value in [('a', 'Alice'), ('b', 'Bob')])
    link = Link('change', 'b', 'r', 'a', 'r', 'CHANGES', 10)
    return Problem('2024-01-01', (source,), mentions, (link,), 'authored_test_scores')


class DecoderExtensionTests(unittest.TestCase):
    def test_independent_uses_same_local_penalty_as_joint(self):
        p = example()
        readings = (Reading('raw_winner', ('example', 'ceo', ''), 'Alice', 5,
                            violations=('unsupported',)),
                    Reading('net_winner', ('example', 'ceo', ''), 'Bob', 3),
                    Reading('unknown', None, None, -10))
        p = replace(p, mentions=(replace(p.mentions[0], readings=readings),), links=(),
                    penalties=(Penalty('unsupported', 4),))
        outputs = [fn(p) for fn in (decode_independent, decode_iterative, decode_joint)]
        self.assertEqual([x.objective for x in outputs], [3, 3, 3])
        self.assertTrue(all(dict(x.reading_ids)['a'] == 'net_winner' for x in outputs))

    def test_all_methods_and_assignment_evaluator_share_hook(self):
        p = example()
        def no_links(selected, chosen):
            return all(link is None for link in chosen)
        for fn in (decode_independent, decode_iterative, decode_joint):
            output = fn(p, feasibility_check=no_links)
            self.assertEqual(output.objective, 4)
            self.assertTrue(all(lid is None for _, lid in output.link_ids))
        self.assertIsNone(evaluate_assignment(p, {'a':'r','b':'r'},
            {'a':None,'b':'change'}, feasibility_check=no_links))

    def test_bad_extension_return_is_not_silently_truthy(self):
        with self.assertRaisesRegex(ValueError, 'bool'):
            decode_joint(example(), feasibility_check=lambda selected, chosen: 1)

    def test_extension_errors_do_not_become_candidate_rejections(self):
        def broken(selected, chosen):
            raise RuntimeError('invalid external state')
        with self.assertRaisesRegex(RuntimeError, 'invalid external state'):
            decode_joint(example(), feasibility_check=broken)


if __name__ == '__main__':
    unittest.main()

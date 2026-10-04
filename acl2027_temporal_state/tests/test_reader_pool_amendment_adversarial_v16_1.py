"""Independent authored controls; never loads natural source pools or labels."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('independent_pool_amendment', ROOT / 'scripts/select_reader_pool_v16_1.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
fixture_spec = importlib.util.spec_from_file_location('independent_pool_fixture', ROOT / 'tests/test_reader_pool_v16.py')
fixtures = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(fixtures)


def gate(content, ancestor=''):
    raw = ('<div xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
           + ancestor + '><table>' + content + '</table></div>').encode()
    root = etree.fromstring(raw); paths = m.views.expanded_paths(root)
    return m.hidden_descendant_gate(root[0], paths, {path: node for node, path in paths.items()})


def empty_gate():
    return gate('<tr><td style="display:none"/></tr>')


class AmendmentIndependentTests(unittest.TestCase):
    def test_empty_and_nonempty_sibling_cues_require_unanimous_permission(self):
        g = gate('<tr><td style="display:none"/><td style="display:none">year</td></tr>')
        self.assertEqual(g['recognized_cue_count'], 2)
        self.assertEqual(g['decision'], 'reject_nonempty_or_uncertain_cue')

    def test_comment_tail_is_counted_but_comment_contents_are_not(self):
        for tail, expected in [('year', 'reject_nonempty_or_uncertain_cue'), (' ', 'permit_empty_descendant_layout')]:
            with self.subTest(tail=tail):
                g = gate('<tr><td style="display:none"><!--not displayed-->' + tail + '</td></tr>')
                self.assertEqual(g['decision'], expected)

    def test_nested_child_tail_is_inside_cue_subtree(self):
        g = gate('<tr><td style="display:none"><span/>unit</td></tr>')
        self.assertEqual(g['decision'], 'reject_nonempty_or_uncertain_cue')

    def test_empty_hook_on_descendant_blocks_permission(self):
        for attribute in ('id=""', 'class=""'):
            with self.subTest(attribute=attribute):
                g = gate('<tr><td style="display:none"><span ' + attribute + '/></td></tr>')
                self.assertIn('id_or_class_attribute_hook_present', g['blocking_reason_counts'])

    def test_ancestor_cue_cannot_be_cleared_by_empty_descendant(self):
        g = gate('<tr><td style="display:none"/></tr>', 'style="display:none"')
        self.assertIn('cue_on_table_or_ancestor', g['blocking_reason_counts'])
        self.assertEqual(g['decision'], 'reject_nonempty_or_uncertain_cue')

    def test_empty_nonfraction_with_nil_still_blocks_permission(self):
        g = gate('<tr><td style="display:none"><ix:nonFraction xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/></td></tr>')
        self.assertEqual(g['decision'], 'reject_nonempty_or_uncertain_cue')
        self.assertGreater(g['subtree_category_counts']['numeric_facts'], 0)

    def test_every_nonvisibility_failure_is_retained_in_order(self):
        table, facts = fixtures.fixture(3)
        table['child_table_ordinals'] = [99]
        table['orphan_cells'] = [{'authored': True}]
        table['rows'][0]['text'] = 'unrelated'
        for fact in facts:
            fact['numeric_status'] = 'unsupported'
            fact['reported_aspects']['period'] = deepcopy(facts[0]['reported_aspects']['period'])
        original = m.base.assess_table(table, facts, 'x' * 12001, descendant_hiding=True)
        amended = m.assess_table(table, facts, 'x' * 12001, empty_gate())
        self.assertEqual(amended['rejection_reasons'], [x for x in original['rejection_reasons'] if x != 'recognized_hiding_cue'])
        self.assertEqual(len(amended['rejection_reasons']), 7)
        self.assertFalse(amended['eligible'])

    def test_recognized_cue_does_not_clear_unknown_table_or_cell_status(self):
        for where in ('table', 'cell'):
            with self.subTest(where=where):
                table, facts = fixtures.fixture()
                target = table if where == 'table' else table['rows'][0]['cells'][0]
                target['visibility']['status'] = 'unknown_authored_status'
                self.assertIn('recognized_hiding_cue', m.assess_table(table, facts, 'Net sales', empty_gate())['rejection_reasons'])

    def test_table_facts_and_gate_are_not_mutated(self):
        table, facts = fixtures.fixture(); g = empty_gate()
        before = deepcopy((table, facts, g))
        a = m.assess_table(table, facts, 'Net sales', g)
        a['empty_descendant_layout_gate']['decision'] = 'changed_return_only'
        self.assertEqual((table, facts, g), before)

    def test_unchanged_author_packet_preserves_hidden_empty_rows_without_typed_facts(self):
        table, facts = fixtures.fixture()
        table['rows'][0]['cells'][0]['visibility']['status'] = 'markup_hiding_cue'
        packet = m.base.author_packet(table, 'authored source text', [], {'source_path': 'authored'})
        self.assertEqual(packet['rows'], table['rows'])
        self.assertNotIn('facts', packet)
        self.assertFalse(packet['rendering_verified'])
        self.assertFalse(packet['complete_evidence_verified'])

    def test_unchanged_selector_rejects_duplicate_or_unsorted_ordinals(self):
        for ordinals in ([2, 1], [1, 1]):
            with self.subTest(ordinals=ordinals), self.assertRaises(m.base.PoolError):
                m.base.choose_tables([{'table_ordinal': n, 'eligible': True} for n in ordinals])

    def test_private_symlink_and_mixed_public_private_paths_refused(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            p = Path(directory); link = p / 'repo'; link.symlink_to(ROOT, target_is_directory=True)
            with self.assertRaisesRegex(m.base.PoolError, 'outside_repository'):
                m.execute(p / 'missing', p, p, p, link / 'new_private', p / 'public')
            with self.assertRaisesRegex(m.base.PoolError, 'outside_private_attempt'):
                m.execute(p / 'missing', p, p, p, p / 'private', p / 'private/public.json')


if __name__ == '__main__':
    unittest.main()

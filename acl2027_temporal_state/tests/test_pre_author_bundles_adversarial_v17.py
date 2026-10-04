"""Independent source-free checks; every value here is an authored fixture."""
from copy import deepcopy
import json
import unittest
from tests.test_pre_author_bundles_v17 import m, SHA, block, fact


class IndependentBundleChecks(unittest.TestCase):
    def test_equal_values_keep_distinct_occurrences_and_overlap_links(self):
        first = block(); second = deepcopy(first); second['block_id'] = 'overlap'
        selected = m.collect_facts([fact(0), fact(1)], [first, second], SHA)
        registry, pack, gate = m.candidate_registry('p', 's', SHA, [first, second], selected)
        self.assertEqual([x['source_fact_ordinal'] for x in registry['facts']], [0, 1])
        self.assertEqual(len(pack['facts']), 2)
        self.assertTrue(all(x['block_ids'] == ['table_1', 'overlap'] for x in registry['facts']))

    def test_hidden_declared_population_and_dimensions_never_fill_population(self):
        f = fact(); f['reported_aspects']['population'] = 'SECRET_POPULATION'
        f['reported_aspects']['dimensions'] = [{'dimension': 'SECRET_DIMENSION'}]
        selected = m.collect_facts([f], [block()], SHA)
        registry, pack, gate = m.candidate_registry('p', 's', SHA, [block()], selected)
        self.assertIsNone(pack['facts'][0]['bindings']['population'])
        self.assertNotIn('SECRET', json.dumps(pack))
        self.assertIn('SECRET_DIMENSION', json.dumps(registry))
        self.assertFalse(registry['facts'][0]['visible_period_unit_scope_verified'])

    def test_invalid_normalized_quantity_retains_full_registry_but_refuses_pack(self):
        f = fact(); f['normalized_value'] = '12.0'
        selected = m.collect_facts([f, fact(1, 55, 60)], [block()], SHA)
        registry, pack, gate = m.candidate_registry('p', 's', SHA, [block()], selected)
        self.assertEqual(len(registry['facts']), 2)
        self.assertIsNone(pack)
        self.assertFalse(gate['interface_shape_valid'])
        self.assertIsNotNone(gate['reason'])

    def test_projection_exact_nested_allowlists(self):
        b = block(); b['typed'] = {'value': 'SECRET'}
        b['rows'][0]['context'] = 'SECRET'; b['rows'][0]['cells'][0]['raw_attributes'] = {'x': 'SECRET'}
        projection = m.author_projection('p', 's', SHA, [b])
        public_block = projection['blocks'][0]
        self.assertEqual(set(public_block), {'block_id', 'kind', 'dom_path', 'anchor', 'text', 'rows'})
        self.assertEqual(set(public_block['anchor']), {'byte_start', 'byte_stop', 'span_sha256'})
        self.assertEqual(set(public_block['rows'][0]), {'text', 'cells'})
        self.assertEqual(set(public_block['rows'][0]['cells'][0]), {'text', 'rowspan', 'colspan'})
        self.assertNotIn('SECRET', json.dumps(projection))
        self.assertIsNone(projection['identity_card']); self.assertFalse(projection['author_release_allowed'])

    def test_special_dependencies_reject_nil_and_unsupported_occurrences(self):
        for kind in ('table', 'table_row'):
            for status in ('nil', 'unsupported'):
                with self.subTest(kind=kind, status=status):
                    with self.assertRaisesRegex(ValueError, 'dependency_would_import'):
                        m.check_dependency(kind, block(), [fact(status=status)])

    def test_nonnumeric_dependency_is_allowed_and_numeric_paragraph_keeps_fact(self):
        for kind in ('table', 'table_row'):
            self.assertIsNone(m.check_dependency(kind, block(), []))
        self.assertIsNone(m.check_dependency('paragraph', block(), [fact()]))
        self.assertEqual(len(m.collect_facts([fact()], [block()], SHA)), 1)


if __name__ == '__main__':
    unittest.main()

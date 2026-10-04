"""Independent authored-only pool boundary and provenance checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_reader_pool_v16 import fixture, pool


class IndependentReaderPoolTests(unittest.TestCase):
    def test_revenue_phrase_cannot_cross_row_boundary(self):
        table, facts = fixture()
        table['rows'][0]['text'] = 'Net'
        second = copy.deepcopy(table['rows'][0])
        second['text'] = 'Sales'
        table['rows'].append(second)
        result = pool.assess_table(table, facts, 'Net\nSales')
        self.assertIn('missing_revenue_row_label', result['rejection_reasons'])

    def test_revenue_phrase_within_one_row_remains_eligible(self):
        table, facts = fixture()
        table['rows'][0]['text'] = 'Net\tSales'
        self.assertTrue(pool.assess_table(table, facts, 'Net Sales')['eligible'])

    def test_period_presence_does_not_fabricate_numeric_usability(self):
        table, facts = fixture(5)
        first_period = copy.deepcopy(facts[0]['reported_aspects']['period'])
        for fact in facts[:4]:
            fact['reported_aspects']['period'] = copy.deepcopy(first_period)
        facts[4]['reported_aspects']['period'] = {'kind': 'duration', 'lexemes': {'startDate': '2020-01-01', 'endDate': '2020-12-31'}}
        facts[4]['numeric_status'], facts[4]['normalized_value'] = 'nil', None
        result = pool.assess_table(table, facts, 'Net sales')
        self.assertTrue(result['eligible'])
        self.assertEqual(result['usable_duration_count'], 4)
        self.assertEqual(result['resolved_duration_period_count'], 2)
        self.assertEqual(result['numeric_status_counts']['nil'], 1)

    def test_unresolved_period_does_not_meet_distinct_period_rule(self):
        table, facts = fixture(5)
        for fact in facts[:4]:
            fact['reported_aspects']['period'] = copy.deepcopy(facts[0]['reported_aspects']['period'])
        facts[-1]['binding_status'] = 'unresolved'
        result = pool.assess_table(table, facts, 'Net sales')
        self.assertIn('fewer_than_2_resolved_duration_periods', result['rejection_reasons'])

    def test_unrecognized_visibility_status_refuses_candidate(self):
        table, facts = fixture()
        table['visibility']['status'] = 'not_evaluated'
        self.assertIn('recognized_hiding_cue', pool.assess_table(table, facts, 'Net sales')['rejection_reasons'])

    def test_source_card_strips_unapproved_machine_fields(self):
        identity = {'source_path': 'issuer_2099.html', 'source_sha256': 'a'*64,
                    'identity_facts': [{'name': 'dei:DocumentFiscalYearFocus', 'value': '2022', 'contextref': 'ctx-secret'},
                                       {'name': 'us-gaap:Revenue', 'value': 'unapproved-value'}],
                    'document_fiscal_year_values': ['2022'], 'filename_matches_document_fiscal_year': False}
        encoded = json.dumps(pool.source_card(identity))
        self.assertNotIn('ctx-secret', encoded)
        self.assertNotIn('unapproved-value', encoded)
        self.assertIn('2022', encoded)

    def test_unbound_parent_path_fails_preflight_before_file_access(self):
        protocol = {'schema_version': 'reader_question_pool_protocol_v16', 'rule': pool.RULE,
                    'external_output_limit_bytes': pool.LIMIT, 'private_metadata_reserve_bytes': pool.RESERVE,
                    'inventory_path': '/outside/unbound.json', 'view_protocol_path': '/outside/other.json',
                    'sources': [], 'code_bindings': {}, 'input_bindings': {}, 'runtime_bindings': {}}
        with tempfile.TemporaryDirectory(dir='/dev/shm') as temporary:
            errors = pool.preflight(protocol, Path(temporary), Path(temporary), Path(temporary))
        self.assertIn('parent_artifact_path_mismatch', errors)


if __name__ == '__main__':
    unittest.main()

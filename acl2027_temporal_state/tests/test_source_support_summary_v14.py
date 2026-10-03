"""Support-review integrity contracts; no source references or model answers read."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import summarize_dense_source_support_v14 as summary


def review():
    return {'case_id': 'case_a', 'question_id': 'q', 'context_sha256': 'a'*64,
            'support_status': 'partial',
            'required_fact_support': [{'fact_id': 'original', 'support': 'supported',
                                       'rationale': 'Explicit source assertion.', 'evidence_locators': ['doc p.1']},
                                      {'fact_id': 'revised', 'support': 'absent',
                                       'rationale': 'The required second fact is missing.', 'evidence_locators': []}],
            'scope_support': {'support': 'partial', 'rationale': 'Only the original scope is supplied.'},
            'unit_support': {'support': 'supported', 'rationale': 'Qualitative facts have no numeric unit.'},
            'specific_omissions': ['The revised required fact.']}


class SourceSupportContracts(unittest.TestCase):
    def setUp(self):
        self.record = review()
        self.case = {k: self.record[k] for k in ('case_id', 'question_id', 'context_sha256')}

    def test_missing_or_duplicate_facts_cannot_shrink_denominator(self):
        summary.validate_review_record(self.record, self.case, ['original', 'revised'])
        for facts in (self.record['required_fact_support'][:1], self.record['required_fact_support']*2):
            broken = {**self.record, 'required_fact_support': facts}
            with self.assertRaises(ValueError): summary.validate_review_record(broken, self.case, ['original', 'revised'])

    def test_review_identity_and_scope_categories_are_required(self):
        bad = deepcopy(self.record); bad['context_sha256'] = 'b'*64
        with self.assertRaises(ValueError): summary.validate_review_record(bad, self.case, ['original', 'revised'])
        bad = deepcopy(self.record); bad['scope_support']['support'] = 'probably supported'
        with self.assertRaises(ValueError): summary.validate_review_record(bad, self.case, ['original', 'revised'])

    def test_fact_locators_and_nonempty_rationale_are_required(self):
        bad = deepcopy(self.record); del bad['required_fact_support'][0]['evidence_locators']
        with self.assertRaises(ValueError): summary.validate_review_record(bad, self.case, ['original', 'revised'])
        bad = deepcopy(self.record); bad['required_fact_support'][1]['rationale'] = '  '
        with self.assertRaises(ValueError): summary.validate_review_record(bad, self.case, ['original', 'revised'])

    def test_duplicate_context_consistency_uses_decisions_not_wording(self):
        other = deepcopy(self.record)
        other['required_fact_support'].reverse()
        other['scope_support']['rationale'] = 'Different wording of the same partial scope judgment.'
        self.assertEqual(summary.assessment_signature(self.record), summary.assessment_signature(other))
        other['required_fact_support'][0]['support'] = 'supported'
        self.assertNotEqual(summary.assessment_signature(self.record), summary.assessment_signature(other))

    def test_context_loader_checks_both_actual_bytes_and_public_projection(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent.parent/'tmp') as temporary:
            path = Path(temporary)/'contexts.jsonl'
            row = {'question_id': 'q', 'condition': 'bm25_paired', 'context': '  source column\n',
                   'context_sha256': summary.text_sha('  source column\n')}
            path.write_text(json.dumps(row)+'\n')
            report = {'external_contexts_sha256': summary.sha(path),
                      'records': [{k: row[k] for k in ('question_id', 'condition', 'context_sha256')}]}
            summary.load_contexts(path, report, 1)
            report['records'].append(dict(report['records'][0]))
            with self.assertRaises(ValueError): summary.load_contexts(path, report, 1)
            report['records'].pop()
            report['records'][0]['context_sha256'] = 'c'*64
            with self.assertRaises(ValueError): summary.load_contexts(path, report, 1)
            report['records'][0]['context_sha256'] = row['context_sha256']
            row['context'] = row['context'].strip()
            path.write_text(json.dumps(row)+'\n')
            report['external_contexts_sha256'] = summary.sha(path)
            with self.assertRaises(ValueError): summary.load_contexts(path, report, 1)


if __name__ == '__main__': unittest.main()

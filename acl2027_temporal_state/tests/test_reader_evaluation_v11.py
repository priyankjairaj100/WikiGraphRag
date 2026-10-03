"""Authored evaluator boundary fixtures; never native predictions or new references."""
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import evaluate_reader_v11 as scoring


class ReaderEvaluationBoundaryTests(unittest.TestCase):
    def answers(self):
        return {'answers': [
            {'question_id': 'Q1', 'exposure': 'physicians_receiving_payments',
             'value': 13.59, 'evidence_ids': ['P4']},
            {'question_id': 'Q2', 'agreement': 'disagree', 'evidence_ids': ['P4']},
            {'question_id': 'Q3', 'exposure': 'payments', 'value': 1.18, 'evidence_ids': ['P3']}]}

    def parse(self, answer=None):
        return scoring.parse_answers(json.dumps(self.answers() if answer is None else answer))

    def exported_row(self):
        content = json.dumps(self.answers())
        row = {key: None for key in scoring.PROJECTION_FIELDS}
        row.update(item_id='article_then_notice', content=content,
                   content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                   content_utf8_bytes=len(content.encode()),
                   content_export_status='reviewed_unchanged_native_text', truncated=False,
                   output_budget_reached=False, stop=True, stop_type='eos', tokens_predicted=150)
        return row

    def test_duplicate_key_rejected_at_every_depth(self):
        for content in ('{"answers":[],"answers":[]}', '{"answers":[{"question_id":"Q1","question_id":"Q1"}]}'):
            with self.assertRaisesRegex(scoring.EvaluationInputError, 'duplicate'):
                scoring.parse_answers(content)

    def test_schema_order_extra_fields_and_fences_rejected(self):
        for kind in ('order', 'extra', 'fence'):
            with self.subTest(kind=kind):
                answer = self.answers()
                if kind == 'order':
                    answer['answers'].reverse()
                elif kind == 'extra':
                    answer['answers'][0]['explanation'] = 'forbidden'
                content = json.dumps(answer)
                if kind == 'fence':
                    content = '```json\n' + content + '\n```'
                with self.assertRaises(scoring.EvaluationInputError):
                    scoring.parse_answers(content)

    def test_boolean_string_nonfinite_and_missing_numeric_rejected(self):
        for number in (True, '13.59', None, float('nan'), float('inf')):
            with self.subTest(number=number):
                answer = self.answers()
                answer['answers'][0]['value'] = number
                with self.assertRaises(scoring.EvaluationInputError):
                    self.parse(answer)

    def test_exact_numeric_comparison_rejects_nearby_value(self):
        _, references = scoring.bindings()
        answer = self.parse()[0]
        answer['value'] = Decimal('13.5900000000000001')
        result = scoring.compare_answer(answer, references['answers'][0], references['adjudications'][0])
        self.assertFalse(result['content_match'])
        answer['value'] = Decimal('13.590')
        self.assertTrue(scoring.compare_answer(answer, references['answers'][0],
                                              references['adjudications'][0])['correct'])

    def test_unknown_duplicate_empty_evidence_rejected(self):
        for ids in (['P5'], ['P4', 'P4'], [], [True]):
            with self.subTest(ids=ids):
                answer = self.answers()
                answer['answers'][1]['evidence_ids'] = ids
                with self.assertRaises(scoring.EvaluationInputError):
                    self.parse(answer)

    def test_q2_p4_sufficient_and_p1_extra_not_penalized(self):
        _, references = scoring.bindings()
        answer = self.parse()[1]
        ref, adjudication = references['answers'][1], references['adjudications'][1]
        self.assertTrue(scoring.compare_answer(answer, ref, adjudication)['correct'])
        answer['evidence_ids'] = ['P1', 'P4']
        result = scoring.compare_answer(answer, ref, adjudication)
        self.assertTrue(result['correct'])
        self.assertEqual(result['additional_valid_evidence_ids'], ['P1'])
        answer['evidence_ids'] = ['P1']
        result = scoring.compare_answer(answer, ref, adjudication)
        self.assertTrue(result['content_match'])
        self.assertFalse(result['correct'])

    def test_uncertain_requires_null_and_does_not_equal_numeric_reference(self):
        _, references = scoring.bindings()
        answer = self.answers()
        answer['answers'][0]['exposure'] = 'uncertain'
        with self.assertRaises(scoring.EvaluationInputError):
            self.parse(answer)
        answer['answers'][0]['value'] = None
        parsed = self.parse(answer)
        self.assertFalse(scoring.compare_answer(parsed[0], references['answers'][0],
                                               references['adjudications'][0])['correct'])

    def test_truncation_and_output_limit_retain_three_failed_questions(self):
        _, references = scoring.bindings()
        for mutation in ('truncated', 'output_budget_reached'):
            row = self.exported_row()
            row[mutation] = True
            result = scoring.score_condition(row, references)
            self.assertFalse(result['schema_valid'])
            self.assertEqual(len(result['questions']), 3)
            self.assertEqual(result['correct_questions'], 0)
            self.assertTrue(all(x['status'] == 'failed' for x in result['questions']))

    def test_one_invalid_answer_is_not_silently_partially_salvaged(self):
        _, references = scoring.bindings()
        row = self.exported_row()
        answer = self.answers()
        answer['answers'][1]['evidence_ids'] = ['P5']
        row['content'] = json.dumps(answer)
        row['content_sha256'] = hashlib.sha256(row['content'].encode()).hexdigest()
        row['content_utf8_bytes'] = len(row['content'].encode())
        result = scoring.score_condition(row, references)
        self.assertEqual(result['correct_questions'], 0)
        self.assertIn('unknown', result['failure'])
        self.assertEqual([x['status'] for x in result['questions']], ['failed'] * 3)


if __name__ == '__main__':
    unittest.main()

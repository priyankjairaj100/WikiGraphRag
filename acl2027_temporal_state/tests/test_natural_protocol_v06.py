"""Leakage and complete-score boundary checks, using only authored fixtures."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prepare = module('test_prepare_natural_v06', 'prepare_natural_protocol_v06.py')
ordinal = module('test_ordinal_v06', 'prepare_ordinal_scoring_v06.py')


def fixture():
    text = 'Acme announced Ada will become CEO on March 1, 2026.'
    source = {'source_id': 'source_a', 'history_id': 'history_a',
              'text': text, 'text_sha256': sha256(text.encode()).hexdigest(),
              'reported_publication_date': '2026-02-01',
              'operational_available_at': '2026-02-02', 'availability_basis': 'authored'}
    request = {'schema_version': 'source_request_v0.6', 'request_id': 'r',
               'history_id': 'history_a', 'information_cutoff': '2026-02-02',
               'extraction_domains': ['organizational_role_occupancy'],
               'scope_filter': None, 'sources': [source]}
    reading = {'candidate_id': 'original_semantic_id', 'subject': 'Acme',
               'relation': 'CEO', 'value': 'Ada', 'scope': '', 'role_qualifier': '',
               'polarity': 'positive', 'modality': 'announced_future',
               'reported_at': '2026-02-01',
               'start': {'lower': '2026-03-01', 'upper': '2026-03-01', 'precision': 'exact_day'},
               'end': {'lower': None, 'upper': None, 'precision': 'unknown'},
               'state_observed_at': [], 'observation_kind': 'effective_boundary',
               'evidence': [{'source_id': 'source_a', 'quote': text}],
               'derivation_note': 'GENERATOR_RATIONALE_SENTINEL'}
    graph = {'schema_version': 'candidate_graph_v0.6',
             'mentions': [{'mention_id': 'original_mention_id', 'source_id': 'source_a',
                           'anchor_quote': text, 'readings': [reading],
                           'unresolved_reason': 'UNRESOLVED_REASON_SENTINEL'}],
             'links': [], 'aliases': [], 'authorities': [], 'essential_support': [], 'unsupported': []}
    return request, graph


class NaturalProtocolTests(unittest.TestCase):
    def test_future_source_rejected_even_if_hash_is_valid(self):
        request, graph = fixture()
        request['sources'][0]['operational_available_at'] = '2026-02-03'
        with self.assertRaisesRegex(ValueError, 'future_source'):
            ordinal.prepare(request, json.dumps(graph))

    def test_source_text_tamper_rejected(self):
        request, graph = fixture()
        request['sources'][0]['text'] += ' Future information.'
        with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
            ordinal.prepare(request, json.dumps(graph))

    def test_source_pack_refuses_extra_label_field(self):
        request, _ = fixture()
        pack = {'purpose': 'test', 'prefixes': {'history_a': '2026-02-02'},
                'sources': request['sources'], 'answers': ['not allowed']}
        with self.assertRaisesRegex(ValueError, 'Unexpected source-pack'):
            prepare.validate_pack(pack, {'history_prefixes': pack['prefixes']})

    def test_neutral_request_removes_generator_notes_and_original_ids(self):
        request, graph = fixture()
        scoring, manifest = ordinal.prepare(request, json.dumps(graph))
        text = json.dumps(scoring)
        for forbidden in ('GENERATOR_RATIONALE_SENTINEL', 'UNRESOLVED_REASON_SENTINEL',
                          'original_semantic_id', 'original_mention_id', 'derivation_note'):
            self.assertNotIn(forbidden, text)
        self.assertEqual({r['kind'] for r in scoring['items']}, {'reading', 'unresolved', 'no_link'})
        self.assertEqual(len(manifest['item_mapping']), 3)
        self.assertEqual(scoring['sources'], request['sources'])

    def score_fixture(self):
        request, graph = fixture()
        scoring, _ = ordinal.prepare(request, json.dumps(graph))
        output = {'schema_version': 'ordinal_scorer_v0.6', 'scores': [
            {'item_id': item['item_id'], 'score': 0, 'evidence': [], 'note': 'Test judgment only.'}
            for item in scoring['items']]}
        return scoring, output

    def test_missing_no_link_score_is_not_defaulted(self):
        request, output = self.score_fixture()
        null_id = next(i['item_id'] for i in request['items'] if i['kind'] == 'no_link')
        output['scores'] = [s for s in output['scores'] if s['item_id'] != null_id]
        with self.assertRaisesRegex(ValueError, 'Missing scoring items'):
            ordinal.validate_scores(json.dumps(output), request)

    def test_duplicate_and_noninteger_scores_rejected(self):
        request, output = self.score_fixture()
        duplicate = deepcopy(output)
        duplicate['scores'].append(deepcopy(duplicate['scores'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate scoring'):
            ordinal.validate_scores(json.dumps(duplicate), request)
        for value in (True, 0.5, 4, -4):
            output['scores'][0]['score'] = value
            with self.assertRaisesRegex(ValueError, 'integer ordinal'):
                ordinal.validate_scores(json.dumps(output), request)

    def test_fabricated_score_quote_rejected(self):
        request, output = self.score_fixture()
        output['scores'][0]['evidence'] = [{'source_id': 'source_a', 'quote': 'Ada is now CEO.'}]
        with self.assertRaisesRegex(ValueError, 'exact_match'):
            ordinal.validate_scores(json.dumps(output), request)

    def test_valid_complete_score_record_does_not_claim_calibration(self):
        request, output = self.score_fixture()
        report = ordinal.validate_scores(json.dumps(output), request)
        self.assertEqual(report['items'], 3)
        self.assertFalse(report['scores_are_calibrated_probabilities'])


if __name__ == '__main__':
    unittest.main()

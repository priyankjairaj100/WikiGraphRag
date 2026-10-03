"""Adversarial prefix-leak, hash, span and correction-binding checks."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'validate_source_annotation_v08.py'
spec = importlib.util.spec_from_file_location('source_annotation_v08', SCRIPT)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def raw(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


class SourceAnnotationValidation(unittest.TestCase):
    def setUp(self):
        text = 'A café plans opening on 2030-01-02. Correction: 41%, not 81%.'
        self.source = {'source_id': 's1', 'exact_version_sha256': 'a' * 64,
                       'text_sha256': v.digest(text.encode()), 'text': text}
        self.capture = {'sources': [{'source_id': 's1', 'history_id': 'h1',
                                    'exact_version_sha256': 'a' * 64,
                                    'extracted_text_sha256': self.source['text_sha256'],
                                    'content_usability': {'provisional_content_usable': True}}]}
        self.delivery = {'prefixes': [{'prefix_id': 'h1_p1', 'history_id': 'h1',
                                      'delivery_batch_index': 1, 'source_ids': ['s1'], 'status': 'ready',
                                      'missing_source_ids': [],
                                      'source_bindings': [{k: self.source[k] for k in ('source_id', 'exact_version_sha256', 'text_sha256')}]}]}
        self.request = {'schema_version': 'source_annotation_request_v0.8', 'history_id': 'h1',
                        'prefix_id': 'h1_p1', 'delivery_batch_index': 1,
                        'capture_manifest_sha256': v.digest(raw(self.capture)),
                        'delivery_manifest_sha256': v.digest(raw(self.delivery)),
                        'sources': [self.source]}
        quote = 'café'
        start = text.index(quote)
        evidence = {'source_id': 's1', 'start': start, 'end': start + len(quote), 'quote': quote}
        self.output = {'schema_version': 'source_annotation_v0.8', 'history_id': 'h1',
                       'prefix_id': 'h1_p1', 'request_sha256': v.digest(raw(self.request)),
                       'eligible_source_ids': ['s1'], 'annotation_kind': 'unversioned_model_source_only_not_gold',
                       'claims': [{'claim_id': 'c1', 'subject': 'café', 'relation': 'plan:opening',
                                   'value': 'opening planned', 'scope': '', 'modality': 'planned',
                                   'assertion_status': 'asserted_by_source', 'temporal_constraints': [],
                                   'evidence': [evidence], 'notes': ''}],
                       'corrections': [], 'links': [], 'unknowns': [], 'representation_limits': []}

    def validate(self, manifests=True):
        return v.validate(raw(self.request), raw(self.output),
                          raw(self.capture) if manifests else None,
                          raw(self.delivery) if manifests else None)

    def rebind(self):
        self.output['request_sha256'] = v.digest(raw(self.request))

    def test_unicode_character_span_and_manifest_binding_pass(self):
        result = self.validate()
        self.assertTrue(result['manifest_bindings_checked'])
        self.assertEqual(result['evidence_span_count'], 1)
        self.assertNotIn('café', json.dumps(result))

    def test_extra_question_context_rejected(self):
        self.request['reference_questions'] = ['Who wins?']
        self.rebind()
        with self.assertRaisesRegex(ValueError, 'context leakage'):
            self.validate()

    def test_future_source_leak_rejected_even_if_request_hash_rebound(self):
        future = deepcopy(self.source)
        future['source_id'] = 's2'
        self.request['sources'].append(future)
        self.output['eligible_source_ids'].append('s2')
        self.rebind()
        with self.assertRaisesRegex(ValueError, 'absent from capture'):
            self.validate()

    def test_future_evidence_rejected(self):
        self.output['claims'][0]['evidence'][0]['source_id'] = 'future'
        with self.assertRaisesRegex(ValueError, 'future source'):
            self.validate()

    def test_mutated_source_text_hash_rejected(self):
        self.request['sources'][0]['text'] += ' Later fact.'
        self.rebind()
        with self.assertRaisesRegex(ValueError, 'text hash mismatch'):
            self.validate()

    def test_rehashed_mutation_rejected_by_capture(self):
        self.request['sources'][0]['text'] += ' Later fact.'
        self.request['sources'][0]['text_sha256'] = v.digest(self.request['sources'][0]['text'].encode())
        self.rebind()
        with self.assertRaisesRegex(ValueError, 'captured text hash mismatch'):
            self.validate()

    def test_byte_offsets_instead_of_character_offsets_rejected(self):
        self.output['claims'][0]['evidence'][0]['end'] += 1
        with self.assertRaisesRegex(ValueError, 'quote/span mismatch'):
            self.validate()

    def test_capture_manifest_mutation_rejected(self):
        self.capture['sources'][0]['exact_version_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'capture manifest hash mismatch'):
            self.validate()

    def test_delivery_prefix_mutation_rejected(self):
        self.delivery['prefixes'][0]['source_ids'] = ['future']
        self.request['delivery_manifest_sha256'] = v.digest(raw(self.delivery))
        self.rebind()
        with self.assertRaisesRegex(ValueError, 'frozen delivery prefix'):
            self.validate()

    def test_blocked_prefix_rejected(self):
        self.delivery['prefixes'][0]['status'] = 'blocked_missing_source'
        self.request['delivery_manifest_sha256'] = v.digest(raw(self.delivery))
        self.rebind()
        with self.assertRaisesRegex(ValueError, 'not ready'):
            self.validate()

    def test_unregistered_correction_link_rejected(self):
        claim = deepcopy(self.output['claims'][0])
        claim['claim_id'] = 'c2'
        self.output['claims'].append(claim)
        self.output['links'] = [{'link_id': 'l1', 'type': 'corrects', 'from_claim_id': 'c2',
                                 'to_claim_id': 'c1', 'interpretation': 'replacement',
                                 'evidence': deepcopy(claim['evidence'])}]
        with self.assertRaisesRegex(ValueError, 'matching explicit correction'):
            self.validate()

    def test_boolean_offset_rejected(self):
        self.output['claims'][0]['evidence'][0]['start'] = True
        with self.assertRaisesRegex(ValueError, 'not boolean'):
            self.validate()

    def test_duplicate_json_key_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            v.strict_json(b'{"claims": [], "claims": [1]}')


if __name__ == '__main__':
    unittest.main()

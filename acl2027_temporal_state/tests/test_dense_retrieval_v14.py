"""Contract checks only: no downloaded model execution or toy accuracy claims."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dense_retrieval_v14 as dense
import fetch_dense_assets_v14 as fetch


class Encoding:
    def __init__(self, ids, active=None):
        self.ids = ids
        self.attention_mask = [1] * len(ids) if active is None else active
        self.type_ids = [0] * len(ids)


class TokenizerFixture:
    """Observable BERT-shaped fixture, not a real tokenizer or token estimate."""
    def __init__(self):
        self.maximum = None; self.padding = False

    def no_padding(self): self.padding = False
    def no_truncation(self): self.maximum = None

    def enable_truncation(self, **kwargs):
        assert kwargs == {'max_length': 512, 'stride': 0, 'strategy': 'longest_first', 'direction': 'right'}
        self.maximum = 512

    def enable_padding(self, **kwargs):
        assert kwargs == {'direction': 'right', 'pad_id': 0, 'pad_type_id': 0, 'pad_token': '[PAD]'}
        self.padding = True

    def encode_batch(self, texts, add_special_tokens=True):
        assert add_special_tokens
        result = []
        for text in texts:
            body = list(range(1000, 1000 + len(text.split())))
            if self.maximum: body = body[:self.maximum - 2]
            result.append(Encoding([101] + body + [102]))
        if self.padding:
            maximum = max(len(r.ids) for r in result)
            result = [Encoding(r.ids + [0] * (maximum - len(r.ids)), [1] * len(r.ids) + [0] * (maximum - len(r.ids))) for r in result]
        return result


def fixture_counter(question, context):
    n = 100 + len(context.split())
    return {'input_tokens': n, 'rendered_prompt_sha256': dense.sha((question + context).encode()),
            'token_ids_sha256': dense.sha(str(n).encode()), 'tokenizer_identity': {'fixture_only': True}}


class DenseContracts(unittest.TestCase):
    def test_pooling_is_cls_not_mean_and_ignores_padding_values(self):
        hidden = np.array([[[3., 4.], [100., -100.], [999., 999.]], [[0., 2.], [7., 3.], [-500., 8.]]])
        actual = dense.cls_l2(hidden, np.array([[1, 1, 0], [1, 1, 0]]), dimension=2)
        np.testing.assert_allclose(actual, [[.6, .8], [0., 1.]])
        self.assertEqual(actual.dtype, np.float32)
        with self.assertRaises(ValueError): dense.cls_l2(hidden, np.array([[0, 1, 0], [1, 1, 0]]), dimension=2)
        with self.assertRaises(ValueError): dense.cls_l2(np.zeros((1, 1, 2)), np.ones((1, 1)), dimension=2)

    def test_encoder_truncation_keeps_sep_audits_original_and_does_not_change_text(self):
        text = ' '.join(['source'] * 600)
        feeds, ledger = dense.token_batch(TokenizerFixture(), [text, 'short'])
        self.assertEqual(len(text.split()), 600)
        self.assertEqual(ledger[0]['original_token_length'], 602)
        self.assertEqual(ledger[0]['encoded_token_length'], 512)
        self.assertEqual(ledger[0]['discarded_encoder_tokens'], 90)
        self.assertTrue(ledger[0]['truncated'])
        self.assertEqual(feeds['input_ids'][0, -1], 102)
        self.assertEqual(ledger[1]['encoded_token_length'], 3)
        self.assertFalse(ledger[1]['truncated'])
        self.assertEqual(int(feeds['attention_mask'][1].sum()), 3)

    def test_query_prefix_does_not_leak_to_passages(self):
        config = dense.load_config(ROOT / 'configs/dense_retriever_candidate_v14.json')
        self.assertEqual(dense.input_text('source text', False, config), 'source text')
        self.assertEqual(dense.input_text('question?', True, config), 'Represent this sentence for searching relevant passages: question?')

    def test_rrf_is_rank_only_one_based_and_deterministic(self):
        a, b = {'chunk_id': 'a'}, {'chunk_id': 'b'}
        scores = dense.fuse_rankings([(9000, b), (-50, a)], [(.8, a), (.7, b)])
        self.assertEqual([s['chunk_id'] for _, s in scores], ['a', 'b'])
        self.assertAlmostEqual(scores[0][0], 1 / 61 + 1 / 62)
        with self.assertRaises(ValueError): dense.fuse_rankings([(1, a)], [(1, b)])
        with self.assertRaises(ValueError): dense.fuse_rankings([(1, a), (0, a)], [(1, a)])

    def test_dense_ties_use_chunk_id_and_nonfinite_scores_fail(self):
        vectors = np.zeros((2, 384), dtype=np.float32); vectors[:, 0] = 1
        query = vectors[0].copy()
        ranked = dense.rank_dense([{'chunk_id': 'b'}, {'chunk_id': 'a'}], vectors, query)
        self.assertEqual([s['chunk_id'] for _, s in ranked], ['a', 'b'])
        query[0] = np.nan
        with self.assertRaises(ValueError): dense.rank_dense([{}, {}], vectors, query)

    def test_asset_size_and_sha_are_both_enforced(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent.parent / 'tmp') as temporary:
            path = Path(temporary) / 'asset'; path.write_bytes(b'abc')
            item = {'relative_path': 'asset', 'bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
            self.assertEqual(fetch.verify(path, item)['bytes'], 3)
            path.write_bytes(b'abd')
            with self.assertRaises(ValueError): fetch.verify(path, item)

    def test_missing_completion_gate_retains_failed_attempt_before_model_load(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent.parent / 'tmp') as temporary:
            path = Path(temporary); gate = path / 'gate.json'; gate.write_text('{}')
            with self.assertRaises(ValueError):
                dense.encode_index(path/'absent_manifest', path/'absent_questions',
                                   ROOT/'configs/dense_retriever_candidate_v14.json', path/'assets', path/'attempt', gate)
            started = json.loads((path/'attempt/attempt_started.json').read_text())
            finished = json.loads((path/'attempt/attempt_finished.json').read_text())
            self.assertEqual(started['status'], 'started_before_model_load')
            self.assertEqual(finished['status'], 'failed')
            self.assertFalse((path/'attempt/index.json').exists())

    def test_completion_gate_checks_actual_receipt_and_allows_retained_budget_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent.parent / 'tmp') as temporary:
            path = Path(temporary); receipt_path = path/'reader.json'; gate_path = path/'gate.json'
            config = ROOT/'configs/dense_retriever_candidate_v14.json'
            receipt = {'schema_version': 'native_structured_reader_receipt_v0.14', 'batch_id': 'main_v14',
                       'status': 'started', 'started_unix_seconds': time.time()-2, 'finished_unix_seconds': time.time()-1,
                       'completion_calls_started': 32, 'raw_responses_returned': 32, 'validated_responses': 32,
                       'completion_processes': 32, 'output_budget_failures': 1}
            gate = {'allow_encoder_execution': True, 'reader_batch_id': 'main_v14', 'reader_status': 'completed',
                    'expected_responses': 32, 'observed_responses': 32, 'observed_attempts': 32,
                    'candidate_config_sha256': fetch.sha_file(config), 'encoder_script_sha256': fetch.sha_file(Path(dense.__file__))}
            def save():
                receipt_path.write_text(json.dumps(receipt))
                gate['completed_reader_artifacts'] = [{'path': str(receipt_path), 'sha256': fetch.sha_file(receipt_path),
                                                       'role': 'native_reader_receipt'}]
                gate_path.write_text(json.dumps(gate))
            save()
            with self.assertRaises(ValueError): dense.execution_gate(gate_path, config)
            receipt['status'] = 'completed'; receipt['validated_responses'] = 31; save()
            with self.assertRaises(ValueError): dense.execution_gate(gate_path, config)
            receipt['validated_responses'] = 32; save()
            self.assertTrue(dense.execution_gate(gate_path, config)['allow_encoder_execution'])

    def test_six_conditions_preserve_source_and_scope_and_reject_stale_index(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent.parent / 'tmp') as temporary:
            out = Path(temporary); pages = []; seeds = []
            for history in ('h1', 'h2'):
                for version in (1, 2):
                    document = f'{history}v{version}'
                    for page_number in (1, 2, 3):
                        text = f'  {history} native column {page_number}    evidence\n'
                        pages.append({'document_id': document, 'history_id': history, 'version_order': version,
                                      'pdf_page': page_number, 'text': text, 'source_page_sha256': dense.sha(text.encode()),
                                      'blocks': [], 'table_regions': [], 'target_spans': [], 'attachments': []})
                        seeds.append({'chunk_id': f'{document}:p{page_number:03}:c01', 'document_id': document,
                                      'history_id': history, 'version_order': version, 'pdf_page': page_number,
                                      'chunk_index': 1, 'char_start': 0, 'char_stop': len(text), 'native_text': text,
                                      'text': ' '.join(text.split()), 'parent_span_ids': []})
            seeds.sort(key=lambda s: s['chunk_id'])
            source = out / 'source.json'; dense.write_json_new(source, {'pages': pages, 'seeds': seeds})
            manifest = out / 'manifest.json'
            dense.write_json_new(manifest, {'external_structured_corpus_path': str(source), 'external_structured_corpus_sha256': fetch.sha_file(source)})
            questions = out / 'questions.json'
            dense.write_json_new(questions, {'questions': [{'question_id': 'q', 'history_id': 'h1', 'question': 'native evidence?',
                                                          'reference_answer': {'must_never_be_used': 'unrelated'}}]})
            config = ROOT / 'configs/dense_retriever_candidate_v14.json'
            config_data = dense.load_config(config)
            matrix = np.zeros((12, 384), dtype=np.float32); matrix[:, 0] = 1
            index_dir = out / 'index'; index_dir.mkdir()
            np.save(index_dir / 'seeds.npy', matrix, allow_pickle=False)
            np.save(index_dir / 'queries.npy', matrix[:1], allow_pickle=False)
            index = {'config_sha256': fetch.sha_file(config), 'structured_manifest_sha256': fetch.sha_file(manifest),
                     'questions_file_sha256': fetch.sha_file(questions), 'script_sha256': fetch.sha_file(Path(dense.__file__)),
                     'seed_vectors_sha256': fetch.sha_file(index_dir / 'seeds.npy'),
                     'query_vectors_sha256': fetch.sha_file(index_dir / 'queries.npy'),
                     'seed_ledger': [{'chunk_id': s['chunk_id'], 'source_seed_text_sha256': dense.sha(s['text'].encode()),
                                      'source_native_text_sha256': dense.sha(s['native_text'].encode()),
                                      'history_id': s['history_id'], 'document_id': s['document_id'],
                                      'encoder_text_sha256': dense.sha(s['text'].encode())} for s in seeds],
                     'query_ledger': [{'question_id': 'q', 'history_id': 'h1', 'question_sha256': dense.sha(b'native evidence?'),
                                       'encoder_text_sha256': dense.sha(dense.input_text('native evidence?', True, config_data).encode())}],
                     'truncated_chunk_ids': [], 'truncated_question_ids': [], 'source_evidence_truncated': False,
                     'seed_count': 12, 'query_count': 1, 'dimension': 384, 'dtype': 'float32'}
            for record in index['seed_ledger'] + index['query_ledger']:
                record.update({'original_token_length': 4, 'encoded_token_length': 4, 'discarded_encoder_tokens': 0,
                               'truncated': False, 'encoder_token_ids_sha256': dense.sha(b'fixture')})
            frozen_assets = [{k: item[k] for k in ('relative_path', 'bytes', 'sha256')} for item in config_data['assets']]
            freeze = {'created_before_model_load': True, 'assets': frozen_assets, 'created_unix_seconds': time.time()-1,
                      'inputs': {k: {'sha256': index[k+'_sha256']} for k in ('config', 'script', 'structured_manifest', 'questions_file')}}
            for key, path in {'lexical_helper': Path(dense.lexical.__file__), 'closure_helper': Path(dense.structural.__file__),
                              'asset_helper': Path(fetch.__file__)}.items():
                freeze['inputs'][key] = {'sha256': fetch.sha_file(path)}
            dense.write_json_new(index_dir / 'input_freeze.json', freeze)
            index['input_freeze_sha256'] = fetch.sha_file(index_dir / 'input_freeze.json')
            index['encoder_identity'] = {'assets': frozen_assets, 'config_sha256': fetch.sha_file(config),
                                         'pooling': 'CLS_first_token_then_L2', 'dimension': 384,
                                         'runtime_distributions': {**config_data['runtime']['existing_distributions'],
                                                                  **{w['distribution']: w['version'] for w in config_data['runtime']['wheels']}},
                                         'providers': ['CPUExecutionProvider']}
            dense.write_json_new(index_dir / 'index.json', index)
            dense.write_json_new(index_dir / 'attempt_started.json', {'started_unix_seconds': time.time()-2})
            dense.write_json_new(index_dir / 'attempt_finished.json', {'status': 'complete', 'finished_unix_seconds': time.time(),
                                                                       'index_sha256': fetch.sha_file(index_dir/'index.json')})
            result = dense.retrieve(manifest, questions, config, index_dir, Path(__file__), config,
                                    out / 'contexts.jsonl', out / 'metadata.json', counter=fixture_counter)
            self.assertEqual(result['context_count'], 6)
            self.assertEqual(len(result['rankings'][0]['rankings']['dense']), 6)
            for row in result['records']:
                self.assertEqual(len(row['selected_seed_ids']), 6)
                self.assertTrue(all(i.startswith('h1') for i in row['selected_seed_ids']))
                self.assertEqual(row['dropped_seeds'], [])
                self.assertTrue(row['both_versions_represented'])
            contexts = [json.loads(line) for line in (out / 'contexts.jsonl').read_text().splitlines()]
            self.assertTrue(all('  h1 native column' in r['context'] for r in contexts))
            self.assertNotIn('reference_answer', (out / 'metadata.json').read_text())
            # Same-shaped but reordered index IDs must fail before ranking.
            index['seed_ledger'].reverse()
            (index_dir / 'index.json').write_text(json.dumps(index))
            (index_dir/'attempt_finished.json').write_text(json.dumps({'status': 'complete', 'finished_unix_seconds': time.time(),
                                                                       'index_sha256': fetch.sha_file(index_dir/'index.json')}))
            with self.assertRaises(ValueError):
                dense.load_index(index_dir, manifest, questions, config, seeds,
                                 [{'question_id': 'q', 'history_id': 'h1', 'question': 'native evidence?'}])


if __name__ == '__main__':
    unittest.main()

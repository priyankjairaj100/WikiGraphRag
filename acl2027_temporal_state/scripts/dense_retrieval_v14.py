#!/usr/bin/env python3
"""Pinned CPU BGE CLS baseline and BM25/RRF ranking with unchanged source closure.

Encode and retrieve are separate stages. Importing this module neither loads a
model nor downloads packages. Reader token counting requires the native callback.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import time

import numpy as np

import fetch_dense_assets_v14 as assets
import retrieve_paired_v13 as lexical
import retrieve_structured_layout_v14 as structural

ROOT = Path(__file__).resolve().parents[1]
RANKERS = ('bm25', 'dense', 'rrf')
REPRESENTATIONS = ('paired', 'closure')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json_new(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')


def external(path: Path):
    if path.resolve().is_relative_to(ROOT.parent):
        raise ValueError('model vectors and source contexts must remain external')


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text())
    m, r = config['model'], config['retrieval']
    expected = (m['embedding_dimension'] == 384 and m['max_sequence_length'] == 512 and
                m['pooling'] == 'CLS_first_token_then_L2' and m['passage_prefix'] == '' and
                m['query_prefix'] == 'Represent this sentence for searching relevant passages: ' and
                r['rrf_k'] == 60 and r['rrf_weights'] == [1, 1] and
                r['bm25_k1'] == lexical.K1 and r['bm25_b'] == lexical.B and
                m['truncation'] == 'right_to_512_including_special_tokens_encoder_only_with_complete_length_ledger' and
                r['rankers'] == list(RANKERS) and r['representations'] == list(REPRESENTATIONS))
    if not expected:
        raise ValueError('candidate differs from the declared fixed baseline')
    if assets.sha_file(Path(structural.__file__)) != r['source_closure_module_sha256']:
        raise ValueError('source closure implementation changed')
    actual = {'evidence_words': structural.MAX_WORDS, 'full_native_input_tokens': structural.MAX_INPUT_TOKENS,
              'output_reserve_tokens': structural.MAX_OUTPUT_TOKENS, 'context_tokens': structural.CONTEXT_TOKENS}
    if config['budgets'] != actual:
        raise ValueError('reader packing budget mismatch')
    return config


def load_source(manifest_path: Path):
    manifest = json.loads(manifest_path.read_text())
    path = Path(manifest['external_structured_corpus_path'])
    if assets.sha_file(path) != manifest['external_structured_corpus_sha256']:
        raise ValueError('structured corpus identity mismatch')
    corpus = json.loads(path.read_text())
    pages, spans, links = structural.source_index(corpus)
    for page in pages.values():
        if sha(page['text'].encode()) != page['source_page_sha256']:
            raise ValueError('source page text mismatch')
    seeds = sorted(corpus['seeds'], key=lambda s: s['chunk_id'])
    if len({s['chunk_id'] for s in seeds}) != len(seeds):
        raise ValueError('duplicate chunk IDs')
    for seed in seeds:
        page = pages[(seed['document_id'], seed['pdf_page'])]
        native = page['text'][seed['char_start']:seed['char_stop']]
        if native != seed['native_text'] or ' '.join(native.split()) != seed['text']:
            raise ValueError('seed no longer equals its original exact source slice')
    return manifest, seeds, pages, spans, links


def cls_l2(hidden: np.ndarray, attention_mask: np.ndarray, dimension=384) -> np.ndarray:
    """Pool index zero, never mean; refuse invalid/empty/nonfinite vectors."""
    hidden = np.asarray(hidden)
    mask = np.asarray(attention_mask)
    if hidden.ndim != 3 or hidden.shape[:2] != mask.shape or hidden.shape[2] != dimension:
        raise ValueError('expected token hidden states [batch, sequence, dimension]')
    if hidden.shape[1] < 1 or not np.all(mask[:, 0] == 1):
        raise ValueError('CLS position must be attended for every sequence')
    vectors = np.asarray(hidden[:, 0, :], dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if not np.all(np.isfinite(vectors)) or not np.all(np.isfinite(norms)) or np.any(norms <= 0):
        raise ValueError('invalid CLS vectors')
    return np.asarray(vectors / norms, dtype=np.float32)


def input_text(text: str, is_query: bool, config: dict) -> str:
    return (config['model']['query_prefix'] if is_query else '') + text


def token_batch(tokenizer, texts: list[str], maximum=512):
    """Audit pre-truncation lengths, then native tokenizer right truncation/padding.

    Truncation is encoder-only. Reader spans are untouched. The saved length ledger
    includes special tokens and the query prefix. No tokenizer token IDs are public.
    """
    tokenizer.no_padding(); tokenizer.no_truncation()
    originals = tokenizer.encode_batch(texts, add_special_tokens=True)
    lengths = [len(e.ids) for e in originals]
    tokenizer.enable_truncation(max_length=maximum, stride=0, strategy='longest_first', direction='right')
    tokenizer.enable_padding(direction='right', pad_id=0, pad_type_id=0, pad_token='[PAD]')
    encoded = tokenizer.encode_batch(texts, add_special_tokens=True)
    ids = np.asarray([e.ids for e in encoded], dtype=np.int64)
    masks = np.asarray([e.attention_mask for e in encoded], dtype=np.int64)
    types = np.asarray([e.type_ids for e in encoded], dtype=np.int64)
    if ids.ndim != 2 or ids.shape[1] > maximum or ids.shape != masks.shape or types.shape != ids.shape:
        raise ValueError('invalid padded/truncated token batch')
    records = []
    for text, original_length, row, mask in zip(texts, lengths, ids, masks):
        encoded_length = int(mask.sum())
        active = row[mask.astype(bool)]
        if encoded_length != min(original_length, maximum) or active[0] != 101 or active[-1] != 102:
            raise ValueError('unexpected BERT truncation or special-token behavior')
        records.append({'encoder_text_sha256': sha(text.encode()), 'original_token_length': original_length,
                        'encoded_token_length': encoded_length, 'discarded_encoder_tokens': original_length - encoded_length,
                        'truncated': original_length > maximum, 'encoder_token_ids_sha256': sha(active.astype('<i8').tobytes())})
    return {'input_ids': ids, 'attention_mask': masks, 'token_type_ids': types}, records


def execution_gate(path: Path, config_path: Path) -> dict:
    gate = json.loads(path.read_text())
    if (gate.get('allow_encoder_execution') is not True or gate.get('reader_batch_id') != 'main_v14' or
            gate.get('reader_status') != 'completed' or gate.get('expected_responses') != 32 or
            gate.get('observed_responses') != 32 or not isinstance(gate.get('observed_attempts'), int) or
            gate.get('observed_attempts', 0) < 32 or
            gate.get('candidate_config_sha256') != assets.sha_file(config_path) or
            gate.get('encoder_script_sha256') != assets.sha_file(Path(__file__))):
        raise ValueError('missing or mismatched root reader-completion authorization')
    completed = gate.get('completed_reader_artifacts', [])
    if not completed:
        raise ValueError('reader-completion gate must bind observed completion artifacts')
    for artifact in completed:
        if assets.sha_file(Path(artifact['path'])) != artifact['sha256']:
            raise ValueError('reader completion artifact changed')
    receipts = [a for a in completed if a.get('role') == 'native_reader_receipt']
    if len(receipts) != 1:
        raise ValueError('completion gate must designate exactly one native reader receipt')
    receipt = json.loads(Path(receipts[0]['path']).read_text())
    if (receipt.get('schema_version') != 'native_structured_reader_receipt_v0.14' or
            receipt.get('batch_id') != 'main_v14' or receipt.get('status') != 'completed' or
            any(receipt.get(k) != 32 for k in ('completion_calls_started', 'raw_responses_returned',
                                              'validated_responses', 'completion_processes')) or
            not isinstance(receipt.get('finished_unix_seconds'), (int, float)) or
            not receipt['started_unix_seconds'] <= receipt['finished_unix_seconds'] <= time.time()):
        raise ValueError('actual native receipt does not establish completed 32-response main batch')
    return gate


class Encoder:
    def __init__(self, config_path: Path, asset_dir: Path, gate_path: Path):
        execution_gate(gate_path, config_path)
        self.config = load_config(config_path)
        self.config_path = config_path
        receipt = assets.fetch(config_path, asset_dir, scope='model')
        if receipt['missing']:
            raise FileNotFoundError('model/tokenizer assets are missing; use the separate authorized fetch stage')
        expected = dict(self.config['runtime']['existing_distributions'])
        expected.update({w['distribution']: w['version'] for w in self.config['runtime']['wheels']})
        versions = {name: importlib.metadata.version(name) for name in expected}
        if versions != expected:
            raise ValueError(f'runtime versions differ from candidate pins: {versions}')
        # These optional native dependencies are imported only for explicitly requested encoding.
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(asset_dir / 'tokenizer.json'))
        if [self.tokenizer.token_to_id(t) for t in ('[PAD]', '[CLS]', '[SEP]')] != [0, 101, 102]:
            raise ValueError('unexpected pinned BERT tokenizer special tokens')
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.config['runtime']['intra_op_threads']
        options.inter_op_num_threads = self.config['runtime']['inter_op_threads']
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(asset_dir / 'onnx/model.onnx'), sess_options=options,
                                           providers=['CPUExecutionProvider'])
        inputs = self.session.get_inputs()
        if {i.name for i in inputs} != set(self.config['runtime']['expected_input_names']) or any(i.type != 'tensor(int64)' for i in inputs):
            raise ValueError('ONNX input graph differs from declared BERT interface')
        # Select only the unique sequence-level hidden-state output. A pooled model
        # output is not interchangeable with the explicitly required CLS operation.
        outputs = [o for o in self.session.get_outputs() if len(o.shape) == 3 and o.shape[-1] == 384]
        if len(outputs) != 1:
            raise ValueError('ONNX graph must expose one [batch,sequence,384] hidden-state output')
        self.output_name = outputs[0].name
        self.identity = {'config_sha256': assets.sha_file(config_path), 'assets': receipt['verified'],
                         'runtime_distributions': versions, 'python': platform.python_version(), 'platform': platform.platform(),
                         'providers': self.session.get_providers(),
                         'graph_inputs': [{'name': i.name, 'type': i.type, 'shape': i.shape} for i in inputs],
                         'hidden_state_output': {'name': outputs[0].name, 'shape': outputs[0].shape},
                         'pooling': 'CLS_first_token_then_L2', 'dimension': 384}

    def encode(self, texts: list[str], *, is_query=False):
        vectors, ledger = [], []
        size = self.config['runtime']['batch_size']
        for start in range(0, len(texts), size):
            batch = [input_text(t, is_query, self.config) for t in texts[start:start + size]]
            feeds, records = token_batch(self.tokenizer, batch)
            hidden = self.session.run([self.output_name], feeds)[0]
            vectors.append(cls_l2(hidden, feeds['attention_mask']))
            ledger.extend(records)
        if not vectors:
            raise ValueError('empty encoder input')
        return np.concatenate(vectors, axis=0), ledger


def encode_index(manifest_path: Path, questions_path: Path, config_path: Path, asset_dir: Path, output_dir: Path, gate_path: Path):
    external(output_dir)
    if output_dir.exists():
        raise FileExistsError('new output directory required')
    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_new(output_dir / 'attempt_started.json', {
        'schema_version': 'dense_encoder_attempt_v0.14', 'status': 'started_before_model_load',
        'started_unix_seconds': time.time(),
        'script_sha256': assets.sha_file(Path(__file__)),
        'input_paths': {k: str(v.resolve()) for k, v in {'manifest': manifest_path, 'questions': questions_path,
                                                       'config': config_path, 'assets': asset_dir, 'execution_gate': gate_path}.items()}})
    try:
        config = load_config(config_path)
        gate = execution_gate(gate_path, config_path)
        manifest, seeds, _, _, _ = load_source(manifest_path)
        questions = lexical.questions_only(questions_path)
        asset_receipt = assets.fetch(config_path, asset_dir, scope='model')
        if asset_receipt['missing']:
            raise FileNotFoundError('model/tokenizer assets missing before freeze')
        bound_paths = {'config': config_path, 'script': Path(__file__), 'structured_manifest': manifest_path,
                       'questions_file': questions_path, 'execution_gate': gate_path,
                       'structured_corpus': Path(manifest['external_structured_corpus_path']),
                       'lexical_helper': Path(lexical.__file__), 'closure_helper': Path(structural.__file__),
                       'asset_helper': Path(assets.__file__)}
        freeze = {'schema_version': 'dense_encoder_input_freeze_v0.14', 'created_before_model_load': True,
                  'created_unix_seconds': time.time(),
                  'inputs': {k: {'path': str(v.resolve()), 'sha256': assets.sha_file(v)} for k, v in bound_paths.items()},
                  'assets': asset_receipt['verified'], 'seed_count': len(seeds), 'query_count': len(questions),
                  'reader_completion_gate': gate}
        write_json_new(output_dir / 'input_freeze.json', freeze)
        model_started = time.monotonic()
        encoder = Encoder(config_path, asset_dir, gate_path)
        model_load_seconds = time.monotonic() - model_started
        passage_started = time.monotonic()
        seed_vectors, seed_ledger = encoder.encode([s['text'] for s in seeds])
        passage_encoding_seconds = time.monotonic() - passage_started
        query_started = time.monotonic()
        query_vectors, query_ledger = encoder.encode([q['question'] for q in questions], is_query=True)
        query_encoding_seconds = time.monotonic() - query_started
        for record, seed in zip(seed_ledger, seeds):
            record.update({'chunk_id': seed['chunk_id'], 'history_id': seed['history_id'], 'document_id': seed['document_id'],
                           'source_seed_text_sha256': sha(seed['text'].encode()), 'source_native_text_sha256': sha(seed['native_text'].encode())})
        for record, question in zip(query_ledger, questions):
            record.update({'question_id': question['question_id'], 'history_id': question['history_id'],
                           'question_sha256': sha(question['question'].encode())})
        if any(assets.sha_file(path) != freeze['inputs'][key]['sha256'] for key, path in bound_paths.items()):
            raise ValueError('input or implementation changed during encoder execution')
        np.save(output_dir / 'seeds.npy', seed_vectors, allow_pickle=False)
        np.save(output_dir / 'queries.npy', query_vectors, allow_pickle=False)
        result = {'schema_version': 'dense_index_v0.14', 'config_sha256': freeze['inputs']['config']['sha256'],
                  'script_sha256': freeze['inputs']['script']['sha256'],
                  'structured_manifest_sha256': freeze['inputs']['structured_manifest']['sha256'],
                  'structured_corpus_sha256': freeze['inputs']['structured_corpus']['sha256'],
                  'questions_file_sha256': freeze['inputs']['questions_file']['sha256'], 'encoder_identity': encoder.identity,
                  'input_freeze_sha256': assets.sha_file(output_dir / 'input_freeze.json'),
                  'elapsed_encoder_seconds': time.monotonic() - started,
                  'timings': {'model_load_seconds': model_load_seconds, 'passage_encoding_seconds': passage_encoding_seconds,
                              'query_encoding_seconds': query_encoding_seconds,
                              'timing_scope': 'single local run; includes batch tokenization and inference; no controlled speed claim'},
                  'seed_count': len(seeds), 'query_count': len(questions), 'dimension': 384, 'dtype': 'float32',
                  'seed_vectors_sha256': assets.sha_file(output_dir / 'seeds.npy'),
                  'query_vectors_sha256': assets.sha_file(output_dir / 'queries.npy'),
                  'truncated_chunk_ids': [r['chunk_id'] for r in seed_ledger if r['truncated']],
                  'truncated_question_ids': [r['question_id'] for r in query_ledger if r['truncated']],
                  'seed_ledger': seed_ledger, 'query_ledger': query_ledger,
                  'source_evidence_truncated': False, 'answer_or_reference_fields_read': False}
        write_json_new(output_dir / 'index.json', result)
        write_json_new(output_dir / 'attempt_finished.json', {'status': 'complete', 'finished_unix_seconds': time.time(),
                                                             'index_sha256': assets.sha_file(output_dir / 'index.json')})
        return result
    except Exception as error:
        write_json_new(output_dir / 'attempt_finished.json', {'status': 'failed', 'error_type': type(error).__name__,
                                                             'finished_unix_seconds': time.time(),
                                                             'error': str(error), 'elapsed_seconds': time.monotonic() - started})
        raise


def rank_dense(pool: list[dict], vectors: np.ndarray, query: np.ndarray):
    if vectors.shape != (len(pool), 384) or query.shape != (384,):
        raise ValueError('dense matrix alignment mismatch')
    scores = vectors @ query
    if not np.all(np.isfinite(scores)):
        raise ValueError('nonfinite dense similarities')
    return sorted([(float(score), seed) for score, seed in zip(scores, pool)], key=lambda p: (-p[0], p[1]['chunk_id']))


def fuse_rankings(first, second, k=60):
    if k != 60:
        raise ValueError('RRF constant is fixed at 60')
    a = {s['chunk_id']: i for i, (_, s) in enumerate(first, 1)}
    b = {s['chunk_id']: i for i, (_, s) in enumerate(second, 1)}
    if len(a) != len(first) or len(b) != len(second) or set(a) != set(b):
        raise ValueError('RRF requires unique complete rankings of the same history pool')
    return sorted([(1.0 / (k + a[s['chunk_id']]) + 1.0 / (k + b[s['chunk_id']]), s) for _, s in first],
                  key=lambda p: (-p[0], p[1]['chunk_id']))


def load_index(index_dir: Path, manifest_path: Path, questions_path: Path, config_path: Path, seeds, questions):
    index = json.loads((index_dir / 'index.json').read_text())
    finished = json.loads((index_dir / 'attempt_finished.json').read_text())
    started = json.loads((index_dir / 'attempt_started.json').read_text())
    if finished.get('status') != 'complete' or finished.get('index_sha256') != assets.sha_file(index_dir / 'index.json'):
        raise ValueError('index has no matching completed encoder-attempt receipt')
    config = load_config(config_path)
    checks = {'config_sha256': assets.sha_file(config_path), 'structured_manifest_sha256': assets.sha_file(manifest_path),
              'questions_file_sha256': assets.sha_file(questions_path), 'script_sha256': assets.sha_file(Path(__file__))}
    if any(index[k] != value for k, value in checks.items()):
        raise ValueError('index provenance differs from current inputs/code')
    freeze_path = index_dir / 'input_freeze.json'
    if assets.sha_file(freeze_path) != index['input_freeze_sha256']:
        raise ValueError('encoder input freeze hash mismatch')
    freeze = json.loads(freeze_path.read_text())
    if not started['started_unix_seconds'] <= freeze['created_unix_seconds'] <= finished['finished_unix_seconds']:
        raise ValueError('encoder attempt/freeze/finish chronology is invalid')
    identity = index['encoder_identity']
    expected_assets = [{k: item[k] for k in ('relative_path', 'bytes', 'sha256')} for item in config['assets']]
    expected_versions = dict(config['runtime']['existing_distributions'])
    expected_versions.update({w['distribution']: w['version'] for w in config['runtime']['wheels']})
    if (freeze.get('created_before_model_load') is not True or freeze['assets'] != expected_assets or
            identity['assets'] != expected_assets or identity['config_sha256'] != checks['config_sha256'] or
            identity['pooling'] != 'CLS_first_token_then_L2' or identity['dimension'] != 384 or
            identity['runtime_distributions'] != expected_versions or identity['providers'] != ['CPUExecutionProvider'] or
            index['source_evidence_truncated'] is not False):
        raise ValueError('encoder identity or asset freeze mismatch')
    for key, value in checks.items():
        if freeze['inputs'][key.removesuffix('_sha256')]['sha256'] != value:
            raise ValueError('index claims differ from its pre-execution freeze')
    for key, path in {'lexical_helper': Path(lexical.__file__), 'closure_helper': Path(structural.__file__),
                      'asset_helper': Path(assets.__file__)}.items():
        if freeze['inputs'][key]['sha256'] != assets.sha_file(path):
            raise ValueError('helper changed since embedding freeze')
    if (index['seed_count'] != len(seeds) or index['query_count'] != len(questions) or
            index['dimension'] != 384 or index['dtype'] != 'float32'):
        raise ValueError('index count/type metadata mismatch')
    arrays = []
    for kind, count in (('seed', len(seeds)), ('query', len(questions))):
        path = index_dir / ('seeds.npy' if kind == 'seed' else 'queries.npy')
        if assets.sha_file(path) != index[kind + '_vectors_sha256']:
            raise ValueError('vector file hash mismatch')
        matrix = np.load(path, allow_pickle=False)
        if matrix.shape != (count, 384) or matrix.dtype != np.float32 or not np.all(np.isfinite(matrix)):
            raise ValueError('invalid vector matrix')
        if not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-5, rtol=1e-5):
            raise ValueError('vectors are not L2 normalized')
        arrays.append(matrix)
    if [r['chunk_id'] for r in index['seed_ledger']] != [s['chunk_id'] for s in seeds]:
        raise ValueError('seed matrix row alignment changed')
    if [r['question_id'] for r in index['query_ledger']] != [q['question_id'] for q in questions]:
        raise ValueError('query matrix row alignment changed')
    for record, seed in zip(index['seed_ledger'], seeds):
        if (record['source_seed_text_sha256'] != sha(seed['text'].encode()) or
                record['source_native_text_sha256'] != sha(seed['native_text'].encode()) or
                record['encoder_text_sha256'] != sha(seed['text'].encode()) or
                record['history_id'] != seed['history_id'] or record['document_id'] != seed['document_id']):
            raise ValueError('embedded seed text changed')
    for record, question in zip(index['query_ledger'], questions):
        if (record['question_sha256'] != sha(question['question'].encode()) or record['history_id'] != question['history_id'] or
                record['encoder_text_sha256'] != sha(input_text(question['question'], True, config).encode())):
            raise ValueError('embedded query identity changed')
    for record in index['seed_ledger'] + index['query_ledger']:
        length = record['original_token_length']
        if (type(length) is not int or length < 2 or record['encoded_token_length'] != min(length, 512) or
                record['discarded_encoder_tokens'] != max(0, length - 512) or record['truncated'] is not (length > 512) or
                not re.fullmatch('[0-9a-f]{64}', record['encoder_token_ids_sha256'])):
            raise ValueError('invalid encoder truncation ledger')
    if (index['truncated_chunk_ids'] != [r['chunk_id'] for r in index['seed_ledger'] if r['truncated']] or
            index['truncated_question_ids'] != [r['question_id'] for r in index['query_ledger'] if r['truncated']]):
        raise ValueError('truncated ID list disagrees with complete length ledger')
    return index, arrays[0], arrays[1]


def retrieve(manifest_path: Path, questions_path: Path, config_path: Path, index_dir: Path,
             adapter_path: Path, adapter_config: Path, external_output: Path, metadata_output: Path, *, counter=None):
    retrieval_started = time.monotonic()
    external(external_output)
    if external_output.exists() or metadata_output.exists():
        raise FileExistsError('refusing to overwrite retrieval outputs')
    config = load_config(config_path)
    manifest, seeds, pages, spans, links = load_source(manifest_path)
    questions = lexical.questions_only(questions_path)
    index, seed_vectors, query_vectors = load_index(index_dir, manifest_path, questions_path, config_path, seeds, questions)
    counter = counter if counter is not None else structural.load_adapter(adapter_path, adapter_config)
    contexts, records, rankings, identity = [], [], [], None
    ranking_seconds = {name: 0.0 for name in RANKERS}
    packing_seconds = 0.0
    for query_number, question in enumerate(questions):
        indices = [i for i, seed in enumerate(seeds) if seed['history_id'] == question['history_id']]
        pool = [seeds[i] for i in indices]
        if not pool:
            raise ValueError('empty supplied history')
        stage_started = time.monotonic()
        bm25 = lexical.rank_bm25(pool, question['question'])
        ranking_seconds['bm25'] += time.monotonic() - stage_started
        stage_started = time.monotonic()
        dense = rank_dense(pool, seed_vectors[indices], query_vectors[query_number])
        ranking_seconds['dense'] += time.monotonic() - stage_started
        stage_started = time.monotonic()
        ranked = {'bm25': bm25, 'dense': dense, 'rrf': fuse_rankings(bm25, dense)}
        ranking_seconds['rrf'] += time.monotonic() - stage_started
        rank_maps = {name: {s['chunk_id']: i for i, (_, s) in enumerate(rows, 1)} for name, rows in ranked.items()}
        score_maps = {name: {s['chunk_id']: score for score, s in rows} for name, rows in ranked.items()}
        # Full source-free ranking ledger allows audits of seed recall and ties.
        rankings.append({'question_id': question['question_id'], 'history_id': question['history_id'],
                         'pool_chunk_count': len(pool), 'rankings': {
                             name: [{'chunk_id': s['chunk_id'], 'rank': i, 'score': score} for i, (score, s) in enumerate(rows, 1)]
                             for name, rows in ranked.items()}})
        for ranker in RANKERS:
            shortlist = sorted(lexical.select(ranked[ranker], 'paired'), key=lambda pair: (-pair[0], pair[1]['chunk_id']))
            for representation in REPRESENTATIONS:
                candidates = [structural.bundle(seed, representation, spans, links) for _, seed in shortlist]
                pack_started = time.monotonic()
                packed = structural.pack(candidates, representation, pages, question['question'], counter)
                packing_seconds += time.monotonic() - pack_started
                current_identity = packed['native_tokens']['tokenizer_identity']
                if identity is None:
                    identity = current_identity
                elif current_identity != identity:
                    raise ValueError('native reader tokenizer changed between conditions')
                selected = packed['selected']
                documents = sorted({b['seed']['document_id'] for b in selected})
                row = {'question_id': question['question_id'], 'history_id': question['history_id'],
                       'condition': ranker + '_' + representation, 'ranker': ranker, 'representation': representation,
                       'query_sha256': sha(question['question'].encode()), 'context_sha256': sha(packed['context'].encode()),
                       'status': packed['status'], 'fixed_shortlist_seed_ids': [s['chunk_id'] for _, s in shortlist],
                       'selected_seed_ids': [b['seed']['chunk_id'] for b in selected], 'dropped_seeds': packed['dropped'],
                       'selected_document_ids': documents, 'both_versions_represented': len(documents) == 2,
                       'evidence_word_count': packed['evidence_word_count'], 'context_words_including_headers': len(packed['context'].split()),
                       'native_tokens': packed['native_tokens'], 'source_spans': packed['source_spans'],
                       'seed_scores': {s['chunk_id']: {name: {'score': score_maps[name][s['chunk_id']], 'rank': rank_maps[name][s['chunk_id']]}
                                                   for name in RANKERS} for _, s in shortlist},
                       'accepted_attachment_ids': sorted({a for b in selected for a in b['accepted_attachment_ids']}),
                       'unresolved_attachment_ids': sorted({a for b in candidates for a in b['unresolved_attachment_ids']}),
                       'cost_evaluations': packed['cost_evaluations']}
                records.append(row)
                contexts.append({**row, 'question': question['question'], 'context': packed['context']})
    external_output.parent.mkdir(parents=True, exist_ok=True)
    with external_output.open('x') as stream:
        for row in contexts:
            stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n')
    report = {'schema_version': 'dense_retrieval_v0.14', 'script_sha256': assets.sha_file(Path(__file__)),
              'config_sha256': assets.sha_file(config_path), 'structured_manifest_sha256': assets.sha_file(manifest_path),
              'structured_corpus_sha256': manifest['external_structured_corpus_sha256'], 'questions_sha256': assets.sha_file(questions_path),
              'index_sha256': assets.sha_file(index_dir / 'index.json'), 'index_directory': str(index_dir.resolve()),
              'lexical_helper_sha256': assets.sha_file(Path(lexical.__file__)),
              'source_closure_helper_sha256': assets.sha_file(Path(structural.__file__)),
              'token_counter_sha256': assets.sha_file(adapter_path), 'token_counter_config_sha256': assets.sha_file(adapter_config),
              'external_contexts_path': str(external_output.resolve()), 'external_contexts_sha256': assets.sha_file(external_output),
              'question_count': len(questions), 'context_count': len(contexts),
              'conditions': [ranker + '_' + representation for ranker in RANKERS for representation in REPRESENTATIONS],
              'timings': {'ranking_seconds': ranking_seconds, 'packing_seconds': packing_seconds,
                          'elapsed_retrieval_seconds_before_metadata_write': time.monotonic() - retrieval_started,
                          'native_session_startup_included': False, 'rrf_seconds_excludes_constituent_rankers': True,
                          'timing_scope': 'single local run; descriptive engineering cost, not a speed comparison'},
              'budgets': config['budgets'], 'rankings': rankings, 'records': records,
              'novel_algorithm_claim': False, 'reference_or_answer_fields_used': False,
              'encoder_truncated_chunk_ids': index['truncated_chunk_ids'],
              'encoder_truncated_question_ids': index['truncated_question_ids'], 'reader_source_evidence_truncated': False}
    write_json_new(metadata_output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    encode = sub.add_parser('encode', help='explicitly execute the pinned encoder after the reader isolation gate')
    retrieval = sub.add_parser('retrieve', help='rank saved vectors and pack exact source contexts; no encoder execution')
    for command in (encode, retrieval):
        command.add_argument('--config', type=Path, default=ROOT / 'configs/dense_retriever_candidate_v14.json')
        command.add_argument('--manifest', type=Path, required=True)
        command.add_argument('--questions', type=Path, required=True)
    encode.add_argument('--assets', type=Path, required=True)
    encode.add_argument('--output', type=Path, required=True)
    encode.add_argument('--execution-gate', type=Path, required=True, help='root receipt binding completed reader batch and exact candidate/code hashes')
    retrieval.add_argument('--index', type=Path, required=True)
    retrieval.add_argument('--token-counter', type=Path, required=True)
    retrieval.add_argument('--token-counter-config', type=Path, required=True)
    retrieval.add_argument('--contexts', type=Path, required=True)
    retrieval.add_argument('--metadata', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'encode':
        result = encode_index(args.manifest, args.questions, args.config, args.assets, args.output, args.execution_gate)
        print(json.dumps({k: result[k] for k in ('seed_count', 'query_count', 'elapsed_encoder_seconds', 'truncated_chunk_ids', 'truncated_question_ids')}))
    else:
        result = retrieve(args.manifest, args.questions, args.config, args.index, args.token_counter,
                          args.token_counter_config, args.contexts, args.metadata)
        print(json.dumps({k: result[k] for k in ('question_count', 'context_count', 'external_contexts_sha256')}))


if __name__ == '__main__':
    main()

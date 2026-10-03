#!/usr/bin/env python3
"""Replay hash-bound source-support summaries; never read native answer contents."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = tuple(r + '_' + c for r in ('bm25', 'dense', 'rrf') for c in ('paired', 'closure'))
CONTEXT_LEVELS = ('absent', 'partial', 'complete')
FACT_LEVELS = ('absent', 'partial', 'supported')


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def text_sha(text): return hashlib.sha256(text.encode()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def require(test, message):
    if not test: raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def categories(values, levels):
    counts = Counter(values)
    require(not (set(counts)-set(levels)), 'unknown support category')
    return {'denominator': sum(counts.values()), **{level: counts[level] for level in levels}}


def numeric_summary(values):
    require(bool(values), 'empty numeric summary')
    return {'count': len(values), 'sum': sum(values), 'minimum': min(values),
            'maximum': max(values), 'mean': sum(values)/len(values)}


def validate_review_record(record, case, expected_fact_ids):
    required = {'case_id', 'question_id', 'context_sha256', 'support_status', 'required_fact_support',
                'scope_support', 'unit_support', 'specific_omissions'}
    require(required <= set(record), 'review record lacks required fields')
    require(all(record[k] == case[k] for k in ('case_id', 'question_id', 'context_sha256')),
            'review record identity differs from frozen packet')
    require(record['support_status'] in CONTEXT_LEVELS, 'invalid overall support category')
    facts = record['required_fact_support']
    require(isinstance(facts, list), 'required_fact_support must be a list')
    ids = [f['fact_id'] for f in facts]
    require(len(ids) == len(set(ids)) and set(ids) == set(expected_fact_ids),
            'review must assess each unchanged required fact exactly once')
    for fact in facts:
        require(fact['support'] in FACT_LEVELS and isinstance(fact.get('rationale'), str) and fact['rationale'].strip(),
                'each fact needs a valid support category and rationale')
        require(isinstance(fact.get('evidence_locators'), list) and
                all(isinstance(x, str) and x.strip() for x in fact['evidence_locators']),
                'each fact must have a list of source evidence locators, possibly empty for missing evidence')
    for axis in ('scope_support', 'unit_support'):
        require(isinstance(record[axis], dict) and record[axis].get('support') in FACT_LEVELS and
                isinstance(record[axis].get('rationale'), str) and record[axis]['rationale'].strip(),
                'scope/unit support require a valid category and rationale')
    require(isinstance(record['specific_omissions'], list) and
            all(isinstance(x, str) and x.strip() for x in record['specific_omissions']),
            'specific_omissions must be a list of nonempty strings')


def assessment_signature(record):
    """Compare categorical decisions, not wording of rationales/locators."""
    return {'support_status': record['support_status'],
            'facts': {f['fact_id']: f['support'] for f in sorted(record['required_fact_support'], key=lambda f: f['fact_id'])},
            'scope_support': record['scope_support']['support'], 'unit_support': record['unit_support']['support']}


def load_contexts(path, report, expected):
    require(sha(path) == report['external_contexts_sha256'], 'external contexts hash mismatch')
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    indexed = {(r['question_id'], r['condition']): r for r in rows}
    public = {(r['question_id'], r['condition']): r for r in report['records']}
    require(len(rows) == len(indexed) == len(report['records']) == len(public) == expected and set(indexed) == set(public),
            'context population mismatch')
    for key, row in indexed.items():
        require(text_sha(row['context']) == row['context_sha256'] == public[key]['context_sha256'],
                'context bytes differ from their recorded hash')
    return indexed, public


def summarize(args):
    bindings = {}

    def bind(label, path, expected_hash=None):
        path = Path(path)
        digest = sha(path)
        if expected_hash is not None: require(digest == expected_hash, f'input hash mismatch: {label}')
        bindings[label] = {'path': str(path.resolve()), 'sha256': digest, 'bytes': path.stat().st_size}
        return path

    protocol = read(bind('protocol', args.protocol))
    require(protocol['natural_qa_generation_authorized'] is False, 'expected source-only protocol')
    require(protocol['conditions'] == list(CONDITIONS), 'six-condition protocol differs')
    for label, item in protocol['inputs'].items():
        bind('protocol_input:' + label, ROOT/item['path'], item['sha256'])
    report = read(bind('retrieval', args.retrieval))
    mapping = read(bind('support_manifest', args.support_manifest))
    require(mapping['retrieval_metadata_sha256'] == sha(args.retrieval), 'support manifest binds another retrieval')
    require(report['conditions'] == list(CONDITIONS) and report['context_count'] == 48 and report['question_count'] == 8,
            'expected complete six-by-eight retrieval population')
    frozen_inputs = protocol['inputs']
    for field, name in (('baseline_support_sha256', 'results/structured_context_support_v14.json'),
                        ('baseline_metadata_sha256', 'results/structured_retrieval_layout_v14.json'),
                        ('questions_sha256', 'data/paired_reader_v13/questions.json')):
        require(mapping[field] == frozen_inputs[name]['sha256'], f'support mapping differs from frozen input: {field}')
    for field, name in (('script_sha256', 'scripts/dense_retrieval_v14.py'),
                        ('config_sha256', 'configs/dense_retriever_candidate_v14.json'),
                        ('structured_manifest_sha256', 'data/structured_reader_v14/corpus_manifest.json'),
                        ('questions_sha256', 'data/paired_reader_v13/questions.json'),
                        ('source_closure_helper_sha256', 'scripts/retrieve_structured_layout_v14.py'),
                        ('token_counter_sha256', 'scripts/native_structured_reader_v14.py'),
                        ('token_counter_config_sha256', 'configs/structured_reader_main_v14.json')):
        require(report[field] == frozen_inputs[name]['sha256'], f'retrieval differs from source-only freeze: {field}')
    require(mapping['script_sha256'] == frozen_inputs['scripts/prepare_dense_support_v14.py']['sha256'], 'packet helper changed')
    require(mapping['contexts_sha256'] == report['external_contexts_sha256'], 'packet context binding mismatch')
    corpus_binding = protocol['external_bindings']['structured_corpus']
    require(report['structured_corpus_sha256'] == corpus_binding['sha256'], 'retrieval corpus differs from frozen source')
    bind('structured_corpus', corpus_binding['path'], corpus_binding['sha256'])
    context_path = bind('contexts', report['external_contexts_path'], report['external_contexts_sha256'])
    contexts, metrics = load_contexts(context_path, report, 48)
    question_ids = sorted({qid for qid, _ in contexts})
    require(len(question_ids) == 8 and set(contexts) == {(q, c) for q in question_ids for c in CONDITIONS},
            'missing six-condition question cells')
    baseline_report_path = bind('baseline_retrieval', ROOT/'results/structured_retrieval_layout_v14.json', mapping['baseline_metadata_sha256'])
    baseline_report = read(baseline_report_path)
    baseline_path = bind('baseline_contexts', baseline_report['external_contexts_path'], mapping['baseline_contexts_sha256'])
    require(mapping['baseline_contexts_sha256'] == protocol['external_bindings']['baseline_contexts']['sha256'],
            'baseline contexts differ from protocol')
    baseline_contexts, _ = load_contexts(baseline_path, baseline_report, 32)
    baseline_review = read(bind('baseline_support', args.baseline_support, mapping['baseline_support_sha256']))
    baseline_by_pair = defaultdict(list)
    for record in baseline_review['records']:
        baseline_by_pair[record['question_id'], record['context_sha256']].append(record)
    references, domains = {}, {}
    for domain in ('plug', 'opera'):
        reference_path = bind('references_' + domain, ROOT/f'data/paired_reader_v13/references_{domain}.json',
                              mapping['packets'][domain]['reference_sha256'])
        for ref in read(reference_path)['references']:
            qid = ref['question_id']; require(qid not in references, 'duplicate reference question')
            references[qid] = ref; domains[qid] = domain
    require(set(references) == set(question_ids), 'reference population differs from retrieval')
    fact_ids = {q: [f['fact_id'] for f in ref['required_facts']] for q, ref in references.items()}
    require(all(len(v) == len(set(v)) and v for v in fact_ids.values()), 'duplicate/empty required fact IDs')
    grades, grade_origin, review_provenance = {}, {}, {}
    reused = mapping['baseline_contexts_reused']
    require(len(reused) == 16 and {(r['question_id'], r['condition']) for r in reused} ==
            {(q, 'bm25_' + c) for q in question_ids for c in ('paired', 'closure')}, 'baseline reuse population differs')
    reused_duplicate_disagreements = []
    for row in reused:
        key = row['question_id'], row['condition']
        require(row['baseline_condition'] == row['condition'].removeprefix('bm25_'), 'baseline representation mapping changed')
        old_key = row['question_id'], row['baseline_condition']
        now, old = contexts[key], baseline_contexts[old_key]
        require(row['exact_context_bytes_equal'] is True and now['context_sha256'] == row['context_sha256'], 'invalid baseline reuse assertion')
        require(now['context'] == old['context'] and now['question'] == old['question'] and now['history_id'] == old['history_id'],
                'actual baseline context/question/scope is no longer identical')
        matches = baseline_by_pair[key[0], row['context_sha256']]
        require(bool(matches), 'reused context lacks an existing source review')
        for record in matches:
            validate_review_record(record, {'case_id': record['case_id'], 'question_id': key[0], 'context_sha256': row['context_sha256']}, fact_ids[key[0]])
        signatures = {canonical(assessment_signature(r)) for r in matches}
        if len(signatures) != 1:
            reused_duplicate_disagreements.append({'question_id': key[0], 'context_sha256': row['context_sha256'],
                                                   'case_ids': sorted(r['case_id'] for r in matches)})
        # Deterministic identity selection is disclosed; disagreements invalidate conclusions.
        record = sorted(matches, key=lambda r: r['case_id'])[0]
        grades[key] = record
        grade_origin[key] = {'kind': 'reused_exact_baseline_context', 'review_case_id': record['case_id'],
                             'matching_case_ids': sorted(r['case_id'] for r in matches)}
    mapped = {r['case_id']: r for r in mapping['records']}
    require(len(mapped) == len(mapping['records']) == 32, 'new source-support mapping must have32 unique cases')
    for domain, review_path in (('plug', args.plug_review), ('opera', args.opera_review)):
        packet_info = mapping['packets'][domain]
        packet_path = bind('packet_' + domain, packet_info['external_path'], packet_info['sha256'])
        packet = read(packet_path); cases = {c['case_id']: c for c in packet['cases']}
        require(len(cases) == len(packet['cases']) == 16, 'review packet must have16 unique cases')
        review = read(bind('review_' + domain, review_path))
        require(isinstance(review.get('provenance'), dict) and review['provenance'], 'review needs nonempty provenance')
        input_packet = review['input_packet']
        require(Path(input_packet['path']).resolve() == packet_path.resolve() and input_packet['sha256'] == packet_info['sha256'],
                'review is not bound to its exact frozen packet')
        review_provenance[domain] = review['provenance']
        reviewed = {r['case_id']: r for r in review['records']}
        require(len(reviewed) == len(review['records']) == 16 and set(reviewed) == set(cases), 'missing/duplicate/extra reviewed case')
        for case_id, case in cases.items():
            require(case_id in mapped and mapped[case_id]['domain'] == domain, 'case mapping/domain mismatch')
            row = mapped[case_id]; key = row['question_id'], row['condition']
            require(domains[key[0]] == domain, 'reference question belongs to a different review domain')
            require(key in contexts and not row['condition'].startswith('bm25_') and key not in grades,
                    'new review condition is not an unmapped dense/RRF context')
            require(case['question_id'] == key[0] and case['context_sha256'] == row['context_sha256'] == contexts[key]['context_sha256'],
                    'packet/context/mapping identity mismatch')
            require(case['received_context'] == contexts[key]['context'] and case['question'] == contexts[key]['question'],
                    'packet text differs from actual received context/question')
            require(canonical(case['reference']) == canonical(references[key[0]]), 'packet changed frozen required reference')
            record = reviewed[case_id]
            validate_review_record(record, case, fact_ids[key[0]])
            grades[key] = record; grade_origin[key] = {'kind': 'new_masked_source_review', 'domain': domain, 'review_case_id': case_id}
    require(set(grades) == set(contexts), 'not all48 conditions have source-support records')
    consistency_groups = defaultdict(list)
    rubric_inconsistencies = []
    for key, record in grades.items():
        consistency_groups[key[0], record['context_sha256']].append((key[1], record))
        if record['support_status'] == 'complete' and (any(f['support'] != 'supported' for f in record['required_fact_support']) or
                any(record[axis]['support'] != 'supported' for axis in ('scope_support', 'unit_support'))):
            rubric_inconsistencies.append({'question_id': key[0], 'condition': key[1],
                                           'reason': 'complete_context_with_non_supported_required_fact_or_scope_or_unit'})
    duplicate_contexts = []
    for (qid, context_hash), rows in sorted(consistency_groups.items()):
        if len(rows) < 2: continue
        signatures = {canonical(assessment_signature(r)) for _, r in rows}
        duplicate_contexts.append({'question_id': qid, 'context_sha256': context_hash,
                                   'conditions': sorted(c for c, _ in rows), 'categorical_assessments_consistent': len(signatures) == 1,
                                   'assessments': {c: assessment_signature(r) for c, r in sorted(rows)}})
    conditions = {}
    per_question, comparisons = [], []
    budgets = protocol['budgets']
    for key, row in metrics.items():
        selected, dropped, shortlist = row['selected_seed_ids'], row['dropped_seeds'], row['fixed_shortlist_seed_ids']
        drop_ids = [d['chunk_id'] for d in dropped]
        require(len(shortlist) == len(set(shortlist)) == 6 and len(selected) == len(set(selected)) and
                len(drop_ids) == len(set(drop_ids)) and not (set(selected)&set(drop_ids)) and set(selected+drop_ids) == set(shortlist),
                'seed/drop accounting differs from the fixed six-seed shortlist')
        require(row['evidence_word_count'] == sum(s['word_count'] for s in row['source_spans']), 'source word charge mismatch')
        count = row['native_tokens']['input_tokens']
        require(row['evidence_word_count'] <= budgets['evidence_words'] and count <= budgets['full_native_input_tokens'] and
                count + budgets['output_reserve_tokens'] <= budgets['context_tokens'], 'final context exceeds matched reader budget')
        require(row['both_versions_represented'] == (len(row['selected_document_ids']) == 2), 'version-retention metadata inconsistent')
    for condition in CONDITIONS:
        keys = [(q, condition) for q in question_ids]
        records = [grades[k] for k in keys]; rows = [metrics[k] for k in keys]
        domains_summary = {}
        for domain in ('plug', 'opera'):
            subset = [grades[k] for k in keys if domains[k[0]] == domain]
            domains_summary[domain] = {'contexts': categories([r['support_status'] for r in subset], CONTEXT_LEVELS),
                                       'required_facts': categories([f['support'] for r in subset for f in r['required_fact_support']], FACT_LEVELS)}
        conditions[condition] = {'contexts': categories([r['support_status'] for r in records], CONTEXT_LEVELS),
                                 'required_facts': categories([f['support'] for r in records for f in r['required_fact_support']], FACT_LEVELS),
                                 'scope': categories([r['scope_support']['support'] for r in records], FACT_LEVELS),
                                 'units': categories([r['unit_support']['support'] for r in records], FACT_LEVELS),
                                 'by_domain': domains_summary, 'evidence_words': numeric_summary([r['evidence_word_count'] for r in rows]),
                                 'native_input_tokens': numeric_summary([r['native_tokens']['input_tokens'] for r in rows]),
                                 'seed_shortlist_denominator': 48, 'selected_seeds': sum(len(r['selected_seed_ids']) for r in rows),
                                 'dropped_seeds': sum(len(r['dropped_seeds']) for r in rows),
                                 'contexts_with_drops': sum(bool(r['dropped_seeds']) for r in rows),
                                 'contexts_with_both_versions': sum(r['both_versions_represented'] for r in rows),
                                 'empty_evidence_contexts': sum(not r['selected_seed_ids'] for r in rows)}
    for qid in question_ids:
        detail = {'question_id': qid, 'domain': domains[qid], 'required_fact_count': len(fact_ids[qid]),
                  'required_fact_ids': sorted(fact_ids[qid]), 'conditions': {}}
        for condition in CONDITIONS:
            key = qid, condition; record = grades[key]; row = metrics[key]
            signature = assessment_signature(record)
            detail['conditions'][condition] = {**signature, 'context_sha256': record['context_sha256'],
                                               'fact_denominator': len(fact_ids[qid]), 'origin': grade_origin[key],
                                               'evidence_words': row['evidence_word_count'], 'native_input_tokens': row['native_tokens']['input_tokens'],
                                               'selected_seed_ids': row['selected_seed_ids'], 'dropped_seeds': row['dropped_seeds'],
                                               'both_versions_represented': row['both_versions_represented'],
                                               'specific_omissions': record['specific_omissions']}
            for baseline_condition in ('bm25_paired', 'bm25_closure'):
                if condition == baseline_condition: continue
                old = assessment_signature(grades[qid, baseline_condition])
                delta = CONTEXT_LEVELS.index(signature['support_status']) - CONTEXT_LEVELS.index(old['support_status'])
                comparisons.append({'question_id': qid, 'condition': condition, 'baseline': baseline_condition,
                                    'matched_representation': condition.split('_')[1] == baseline_condition.split('_')[1],
                                    'context_support_before': old['support_status'], 'context_support_after': signature['support_status'],
                                    'ordinal_category_change': 'improved' if delta > 0 else 'worse' if delta < 0 else 'same',
                                    'fact_changes': [{'fact_id': fact, 'before': old['facts'][fact], 'after': signature['facts'][fact]}
                                                     for fact in sorted(fact_ids[qid]) if old['facts'][fact] != signature['facts'][fact]],
                                    'scope_before': old['scope_support'], 'scope_after': signature['scope_support'],
                                    'unit_before': old['unit_support'], 'unit_after': signature['unit_support'],
                                    'context_bytes_identical': metrics[qid, condition]['context_sha256'] == metrics[qid, baseline_condition]['context_sha256']})
        per_question.append(detail)
    index_dir = Path(report['index_directory'])
    index = read(bind('index', index_dir/'index.json', report['index_sha256']))
    finished = read(bind('encoder_attempt_finished', index_dir/'attempt_finished.json'))
    require(finished['status'] == 'complete' and finished['index_sha256'] == report['index_sha256'], 'encoder attempt incomplete')
    bind('encoder_input_freeze', index_dir/'input_freeze.json', index['input_freeze_sha256'])
    bind('encoder_attempt_started', index_dir/'attempt_started.json')
    require(index['seed_count'] == 2306 and index['query_count'] == 8, 'unexpected encoder population')
    require(index['truncated_chunk_ids'] == report['encoder_truncated_chunk_ids'] and
            index['truncated_question_ids'] == report['encoder_truncated_question_ids'], 'truncation index/retrieval mismatch')
    seed_ledger, query_ledger = index['seed_ledger'], index['query_ledger']
    require(len(seed_ledger) == 2306 and len(query_ledger) == 8, 'incomplete encoder length ledger')
    require(index['truncated_chunk_ids'] == [r['chunk_id'] for r in seed_ledger if r['truncated']] and
            index['truncated_question_ids'] == [r['question_id'] for r in query_ledger if r['truncated']], 'truncation ID list mismatch')
    truncation = {'seed_denominator': 2306, 'truncated_seeds': len(index['truncated_chunk_ids']),
                  'query_denominator': 8, 'truncated_queries': len(index['truncated_question_ids']),
                  'truncated_chunk_ids': index['truncated_chunk_ids'], 'truncated_question_ids': index['truncated_question_ids'],
                  'passage_original_tokens': numeric_summary([r['original_token_length'] for r in seed_ledger]),
                  'passage_encoded_tokens': numeric_summary([r['encoded_token_length'] for r in seed_ledger]),
                  'query_original_tokens': numeric_summary([r['original_token_length'] for r in query_ledger]),
                  'query_encoded_tokens': numeric_summary([r['encoded_token_length'] for r in query_ledger]),
                  'source_evidence_truncated': False}
    packing = read(bind('packing_runtime', args.packing_runtime))
    require(packing['status'] == 'complete' and packing['context_count'] == 48 and packing['metadata_sha256'] == sha(args.retrieval),
            'native packing completion receipt mismatch')
    require(packing['native_completion_calls'] == 0 and packing['encoder_loaded'] is False and packing['native_tokenizer_sessions'] == 1,
            'packing should use exactly one tokenizer session without encoder or answer generation')
    require(packing['wrapper_sha256'] == frozen_inputs['scripts/run_dense_retrieval_v14.py']['sha256'], 'packing wrapper changed')
    for packing_field, report_field in (('encoder_retriever_sha256', 'script_sha256'),
                                        ('native_adapter_sha256', 'token_counter_sha256'),
                                        ('reader_config_sha256', 'token_counter_config_sha256'),
                                        ('external_contexts_sha256', 'external_contexts_sha256')):
        require(packing[packing_field] == report[report_field], f'packing/retrieval provenance mismatch: {packing_field}')
    require(packing['inner_timings'] == report['timings'], 'packing timing receipts differ')
    disagreements = [r for r in duplicate_contexts if not r['categorical_assessments_consistent']]
    valid = not (disagreements or rubric_inconsistencies or reused_duplicate_disagreements)
    return {'schema_version': 'dense_source_support_summary_v0.14', 'script_sha256': sha(__file__),
            'status': 'complete_consistent_source_support' if valid else 'review_inconsistency_requires_explicit_resolution',
            'counts_valid_for_unqualified_source_support_conclusions': valid,
            'input_bindings': bindings, 'review_provenance': review_provenance,
            'population': {'development_questions': 8, 'conditions': 6, 'contexts': 48, 'new_review_cases': 32,
                           'exact_baseline_reuse_cases': 16, 'required_fact_count_per_condition': sum(map(len, fact_ids.values()))},
            'conditions': conditions, 'per_question': per_question, 'comparisons_with_bm25_controls': comparisons,
            'duplicate_context_audit': {'group_count': len(duplicate_contexts), 'inconsistent_group_count': len(disagreements),
                                        'groups': duplicate_contexts, 'ambiguous_reused_review_groups': reused_duplicate_disagreements},
            'rubric_inconsistencies': rubric_inconsistencies, 'encoder_truncation': truncation,
            'timings': {'index': index['timings'], 'index_total_elapsed_seconds': index['elapsed_encoder_seconds'],
                        'retrieval': report['timings'], 'native_wrapper_total_seconds': packing['elapsed_seconds_including_native_session']},
            'interpretation_limits': ['Source support only; no natural QA results are inferred or evaluated.',
                                      'Eight reused development questions; no held-out generalization or significance claim.',
                                      'Model-assisted condition-label masking is not independent human gold or statistical blindness.',
                                      'All original grades remain preserved; no favorable automatic adjudication of duplicates.',
                                      'BGE small and RRF are established baselines, not a novel algorithm or state-of-the-art claim.',
                                      'Single-run timing metadata are descriptive costs, not a controlled latency comparison.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrieval', type=Path, default=ROOT/'results/dense_retrieval_v14.json')
    parser.add_argument('--support-manifest', type=Path, default=ROOT/'results/dense_source_support_manifest_v14.json')
    parser.add_argument('--plug-review', type=Path, default=ROOT/'results/dense_source_support_plug_v14.json')
    parser.add_argument('--opera-review', type=Path, default=ROOT/'results/dense_source_support_opera_v14.json')
    parser.add_argument('--baseline-support', type=Path, default=ROOT/'results/structured_context_support_v14.json')
    parser.add_argument('--protocol', type=Path, default=ROOT/'data/dense_retrieval_v14/source_coverage_protocol.json')
    parser.add_argument('--packing-runtime', type=Path, default=ROOT/'results/dense_packing_runtime_v14.json')
    parser.add_argument('--output', type=Path, default=ROOT/'results/dense_source_support_summary_v14.json')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not args.check and args.output.exists(): raise FileExistsError('refusing to overwrite summary')
    result = summarize(args)
    encoded = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)+'\n'
    if args.check:
        require(args.output.read_text() == encoded, 'summary does not exactly replay from bound inputs')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream: stream.write(encoded)
    print(json.dumps({'status': result['status'], 'contexts': result['population']['contexts'],
                      'duplicate_inconsistencies': result['duplicate_context_audit']['inconsistent_group_count'],
                      'summary_sha256': sha(args.output), 'check': args.check}))


if __name__ == '__main__': main()

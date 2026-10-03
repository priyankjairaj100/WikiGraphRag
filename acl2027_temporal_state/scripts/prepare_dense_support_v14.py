#!/usr/bin/env python3
"""Prepare new condition-masked source-support packets; never read native answers.

Requires all 48 contexts, and exact identity of the 16 BM25 controls with the
active source-exact v14 contexts before reusing their existing source review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = tuple(r + '_' + c for r in ('bm25', 'dense', 'rrf') for c in ('paired', 'closure'))
NEW_CONDITIONS = tuple(c for c in CONDITIONS if not c.startswith('bm25_'))
REFERENCE_SHA256 = {'plug': '682a7af7fcef05bcc1a0ad16bca1874e2e7b5078db982b9e4a51b46dcbd91dbc',
                    'opera': '2cbe0e728609073bf33c28c0a3f8876063635db6d75bd0bba89e49997e4edc66'}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def text_sha(text): return hashlib.sha256(text.encode()).hexdigest()
def read(path): return json.loads(Path(path).read_text())


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False); stream.write('\n')


def verified_contexts(path: Path, metadata_path: Path, expected_count: int):
    metadata = read(metadata_path)
    if sha(path) != metadata['external_contexts_sha256']:
        raise ValueError('context file differs from its retrieval metadata')
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    indexed = {(r['question_id'], r['condition']): r for r in rows}
    public = {(r['question_id'], r['condition']): r for r in metadata['records']}
    if len(rows) != expected_count or len(indexed) != expected_count or set(public) != set(indexed):
        raise ValueError('missing or duplicate context/metadata cells')
    for key, row in indexed.items():
        if text_sha(row['context']) != row['context_sha256'] or row['context_sha256'] != public[key]['context_sha256']:
            raise ValueError('individual context identity mismatch')
    return metadata, indexed


def prepare(contexts_path: Path, metadata_path: Path, baseline_contexts: Path, baseline_metadata: Path,
            baseline_support: Path, questions_path: Path, references: dict[str, Path], output_dir: Path, manifest_path: Path):
    if output_dir.resolve().is_relative_to(ROOT.parent):
        raise ValueError('source-bearing review packets must remain outside the repository')
    destinations = {domain: output_dir / f'context_support_{domain}.json' for domain in ('plug', 'opera')}
    if manifest_path.exists() or any(p.exists() for p in destinations.values()):
        raise FileExistsError('new packet and manifest paths required')
    metadata, current = verified_contexts(contexts_path, metadata_path, 48)
    baseline, old = verified_contexts(baseline_contexts, baseline_metadata, 32)
    qhash = sha(questions_path)
    if metadata['questions_sha256'] != qhash or baseline['questions_sha256'] != qhash:
        raise ValueError('question-file binding changed between current and baseline retrieval')
    questions = {q['question_id']: {k: q[k] for k in ('question_id', 'history_id', 'question')}
                 for q in read(questions_path)['questions']}
    if len(questions) != 8 or set(current) != {(q, c) for q in questions for c in CONDITIONS}:
        raise ValueError('expected exactly eight questions and all six conditions')
    reused = []
    for qid, question in questions.items():
        for condition in ('paired', 'closure'):
            now, before = current[qid, 'bm25_' + condition], old[qid, condition]
            if (now['context'] != before['context'] or now['question'] != before['question'] or
                    now['question'] != question['question'] or now['history_id'] != question['history_id'] or
                    before['history_id'] != question['history_id']):
                raise ValueError('BM25 control bytes/question/scope differ: old source review cannot be reused')
            reused.append({'question_id': qid, 'condition': 'bm25_' + condition, 'baseline_condition': condition,
                           'context_sha256': now['context_sha256'], 'exact_context_bytes_equal': True})
    support = read(baseline_support)
    reviewed_hashes = {(r['question_id'], r['context_sha256']) for r in support['records']}
    if not {(r['question_id'], r['context_sha256']) for r in reused}.issubset(reviewed_hashes):
        raise ValueError('old support audit does not cover all identical BM25 control contexts')
    all_references = {}
    for domain, path in references.items():
        if sha(path) != REFERENCE_SHA256[domain]:
            raise ValueError('reference differs from the frozen v13 source reference')
        rows = read(path)['references']
        if len(rows) != 4 or len({r['question_id'] for r in rows}) != 4:
            raise ValueError('each reference domain must contain exactly four unique questions')
        for reference in rows:
            qid = reference['question_id']
            if qid in all_references:
                raise ValueError('question belongs to more than one reference domain')
            all_references[qid] = (domain, reference)
    if set(all_references) != set(questions):
        raise ValueError('reference and question populations differ')
    cases = {domain: [] for domain in destinations}; mapping = []
    for qid in sorted(questions):
        domain, reference = all_references[qid]
        for condition in NEW_CONDITIONS:
            row = current[qid, condition]
            if row['question'] != questions[qid]['question'] or row['history_id'] != questions[qid]['history_id']:
                raise ValueError('new context question/scope mismatch')
            case_id = 'case_' + text_sha('dense-source-mask-v14:' + qid + '__' + condition)[:16]
            cases[domain].append({'case_id': case_id, 'question_id': qid, 'question': questions[qid]['question'],
                                  'reference': reference, 'received_context': row['context'], 'context_sha256': row['context_sha256']})
            mapping.append({'case_id': case_id, 'question_id': qid, 'domain': domain, 'condition': condition,
                            'ranker': condition.split('_')[0], 'representation': condition.split('_')[1],
                            'context_sha256': row['context_sha256']})
    packets = {}
    for domain in destinations:
        if len(cases[domain]) != 16:
            raise ValueError('each domain must receive sixteen new source-support cases')
        packets[domain] = {
            'schema_version': 'masked_dense_source_support_v0.14',
            'instructions': 'Review exact received source context against the frozen references, with no native answers. '
                            'Do not inspect condition mappings, ranking ledgers, other conditions or answer outputs. '
                            'Preserve document/version, year, unit and scope requirements; count missing facts rather than infer them. '
                            'This is model-assisted development review with prior project familiarity, not independent human gold or statistical blindness.',
            'required_output_fields': ['case_id', 'question_id', 'context_sha256', 'support_status', 'required_fact_support',
                                       'scope_support', 'unit_support', 'specific_omissions'],
            'output_contract': {'support_status': ['complete', 'partial', 'absent'],
                                'required_fact_support': {'fact_id': 'frozen reference fact identifier',
                                                          'support': ['supported', 'partial', 'absent'], 'rationale': 'exact source justification'},
                                'scope_support': 'explain supported and missing scope bindings',
                                'unit_support': 'explain supported, missing, or inapplicable units',
                                'specific_omissions': 'list specific missing facts/headers/qualifiers'},
            'cases': sorted(cases[domain], key=lambda c: c['case_id'])}
    # Only after every identity/completeness gate passes are either packets written.
    for domain, path in destinations.items():
        write_new(path, packets[domain])
    report = {'schema_version': 'dense_source_support_manifest_v0.14', 'script_sha256': sha(__file__),
              'contexts_sha256': sha(contexts_path), 'retrieval_metadata_sha256': sha(metadata_path),
              'questions_sha256': qhash, 'baseline_contexts_sha256': sha(baseline_contexts),
              'baseline_metadata_sha256': sha(baseline_metadata), 'baseline_support_sha256': sha(baseline_support),
              'baseline_contexts_reused': reused, 'new_case_count': 32, 'all_retrieval_contexts_preserved': 48,
              'native_answers_loaded': False, 'condition_labels_masked_in_packets': True,
              'statistical_blindness_claimed': False,
              'packets': {domain: {'external_path': str(path.resolve()), 'sha256': sha(path), 'case_count': 16,
                                    'reference_sha256': sha(references[domain])} for domain, path in destinations.items()},
              'records': sorted(mapping, key=lambda r: r['case_id'])}
    write_new(manifest_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contexts', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--baseline-contexts', type=Path, required=True)
    parser.add_argument('--baseline-metadata', type=Path, default=ROOT/'results/structured_retrieval_layout_v14.json')
    parser.add_argument('--baseline-support', type=Path, default=ROOT/'results/structured_context_support_v14.json')
    parser.add_argument('--questions', type=Path, default=ROOT/'data/paired_reader_v13/questions.json')
    parser.add_argument('--references-plug', type=Path, default=ROOT/'data/paired_reader_v13/references_plug.json')
    parser.add_argument('--references-opera', type=Path, default=ROOT/'data/paired_reader_v13/references_opera.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.contexts, args.metadata, args.baseline_contexts, args.baseline_metadata, args.baseline_support,
                     args.questions, {'plug': args.references_plug, 'opera': args.references_opera}, args.output_dir, args.manifest)
    print(json.dumps({'new_case_count': result['new_case_count'], 'baseline_reuse_count': len(result['baseline_contexts_reused'])}))


if __name__ == '__main__':
    main()

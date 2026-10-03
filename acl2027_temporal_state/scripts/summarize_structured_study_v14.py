#!/usr/bin/env python3
"""Hash-bound descriptive joins over the frozen32-cell development study."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ('ordinary', 'paired', 'parent', 'closure')
AXES = ('reference_content_correct', 'scope_correct', 'citation_grounded',
        'format_ok', 'appropriate_abstention', 'native_output_budget_failure')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def summarize():
    inputs = {}
    def read(name):
        p = ROOT / name
        inputs[name] = sha(p)
        return json.loads(p.read_text())

    questions = read('data/paired_reader_v13/questions.json')['questions']
    read('data/paired_reader_v13/scoring_contract.json')
    question_map = {q['question_id']: q for q in questions}
    context_map = read('data/structured_reader_v14/context_review_layout_manifest.json')['records']
    support = read('results/structured_context_support_v14.json')
    support_cases = {r['case_id']: r for r in support['records']}
    supports = {}
    for r in context_map:
        s = support_cases[r['case_id']]
        require(s['context_sha256'] == r['context_sha256'], 'Source review context hash mismatch')
        supports[r['question_id'], r['condition']] = s
    require(len(supports) == 32, 'Expected32 source reviews')
    retrieval = read('results/structured_retrieval_layout_v14.json')
    retrieved = {(r['question_id'], r['condition']): r for r in retrieval['records']}
    native_dir = 'data/native_structured_reader_v14/main_attempt01/'
    frozen = read(native_dir + 'frozen_inputs.json')
    prompts = read(native_dir + 'rendered_prompts.json')
    receipt = read('results/native_structured_main_v14_receipt.json')
    require(receipt['status'] == 'completed' and receipt['validated_responses'] == 32,
            'All32 main responses must be preserved and validated')
    native = {}
    native_indices = {}
    for i, rid in enumerate(frozen['execution_order']):
        projection = read(native_dir + f'process_{i:02d}.response_projection.json')
        require(projection['request_id'] == rid == prompts[i]['request_id'], 'Native order mismatch')
        native[rid] = (projection, prompts[i]['prompt'])
        native_indices[rid] = i
    records = []
    for domain in ('plug', 'opera'):
        mapping = read(f'data/structured_reader_v14/grading_{domain}_manifest.json')
        grades = read(f'results/grades_structured_{domain}_v14.json')
        cases = {g['case_id']: g for g in grades['cases']}
        refs = read(f'data/paired_reader_v13/references_{domain}.json')['references']
        references = {r['question_id']: r for r in refs}
        require(mapping['reference_sha256'] == inputs[f'data/paired_reader_v13/references_{domain}.json']
                and mapping['scoring_contract_sha256'] == inputs['data/paired_reader_v13/scoring_contract.json'],
                'Grading reference or contract hash differs')
        require(grades['grading_packet_sha256'] == mapping['packet_sha256'], 'Grader packet hash differs')
        require(len(cases) == len(mapping['records']) == 16, 'Expected16 cases per domain')
        require(set(cases) == {r['case_id'] for r in mapping['records']}, 'Grading IDs differ')
        for m in mapping['records']:
            case = cases[m['case_id']]
            qid, condition = m['question_id'], m['condition']
            require(m['request_id'] == qid + '__' + condition, 'Mapping request ID differs')
            projection, prompt = native[m['request_id']]
            i = native_indices[m['request_id']]
            require(m['native_response_file_sha256'] == receipt['external_artifacts'][f'process_{i:02d}.response.json']['sha256']
                    and m['response_projection_sha256'] == inputs[native_dir + f'process_{i:02d}.response_projection.json'],
                    'Grading response provenance differs')
            ctx = retrieved[qid, condition]
            sup = supports[qid, condition]
            require(case['question_id'] == qid, 'Question mismatch')
            require(case['raw_answer_sha256'] == m['content_sha256'] == projection['content_sha256'],
                    'Raw answer hash differs')
            require(case['received_context_sha256'] == m['context_sha256'] == ctx['context_sha256']
                    == sup['context_sha256'], 'Context hash differs')
            require(prompt['input_tokens'] == ctx['native_tokens']['input_tokens']
                    and prompt['input_token_ids_sha256'] == ctx['native_tokens']['token_ids_sha256']
                    and prompt['rendered_prompt_sha256'] == ctx['native_tokens']['rendered_prompt_sha256'],
                    'Selected context and native input differ')
            for axis in AXES:
                require(type(case[axis]) is bool, 'Nonboolean grade axis: ' + axis)
            require(case['native_output_budget_failure'] == projection['output_budget_failure'],
                    'Output budget grade differs from native trace')
            facts = case['required_fact_assessments']
            reference_facts = references[qid]['required_facts']
            expected_ids = {f['fact_id'] for f in reference_facts}
            require(len(facts) == len(expected_ids) and {f['fact_id'] for f in facts} == expected_ids,
                    'Required fact denominator changed')
            records.append({'question_id': qid, 'history_id': question_map[qid]['history_id'],
                'condition': condition, 'request_id': m['request_id'], 'case_id': m['case_id'],
                'content_sha256': m['content_sha256'], 'context_sha256': m['context_sha256'],
                'context_support': sup['support_status'], **{k: case[k] for k in AXES},
                'joint_content_scope_citation': all(case[k] for k in AXES[:3]),
                'required_fact_count': len(facts),
                'fact_status_counts': dict(sorted(Counter(f['status'] for f in facts).items())),
                'unsupported_assertion_count': len(case['unsupported_assertions']),
                'input_tokens': prompt['input_tokens'], 'output_tokens': projection['tokens_predicted'],
                'input_token_ids_sha256': prompt['input_token_ids_sha256'],
                'evidence_words': ctx['evidence_word_count'], 'dropped_seed_count': len(ctx['dropped_seeds']),
                'both_versions_represented': ctx['both_versions_represented']})
    require({(r['question_id'], r['condition']) for r in records}
            == {(q['question_id'], c) for q in questions for c in CONDITIONS}, 'Incomplete factorial coverage')
    records.sort(key=lambda r: (r['question_id'], CONDITIONS.index(r['condition'])))
    aggregation = []
    for condition in CONDITIONS:
        rows = [r for r in records if r['condition'] == condition]
        complete = [r for r in rows if r['context_support'] == 'complete']
        aggregation.append({'condition': condition, 'questions': 8,
            **{a: sum(r[a] for r in rows) for a in (*AXES, 'joint_content_scope_citation')},
            'context_support_counts': dict(sorted(Counter(r['context_support'] for r in rows).items())),
            'complete_context_cases': len(complete),
            'complete_context_joint_passes': sum(r['joint_content_scope_citation'] for r in complete),
            'unsupported_assertions': sum(r['unsupported_assertion_count'] for r in rows),
            'input_token_range': [min(r['input_tokens'] for r in rows), max(r['input_tokens'] for r in rows)],
            'evidence_word_range': [min(r['evidence_words'] for r in rows), max(r['evidence_words'] for r in rows)],
            'dropped_seeds': sum(r['dropped_seed_count'] for r in rows)})
    prior = read('results/paired_study_summary_v13.json')
    old = {(r['question_id'], r['condition']): r for r in prior['records'] if r['batch'] == 'layout'}
    reader_contrasts = []
    for r in records:
        if r['condition'] not in ('ordinary', 'paired'):
            continue
        previous = old[r['question_id'], r['condition']]
        require(previous['context_sha256'] == r['context_sha256'], 'Reader comparison changed source context')
        reader_contrasts.append({'question_id': r['question_id'], 'condition': r['condition'],
            'identical_context_sha256': r['context_sha256'],
            **{a: {'v13': previous[a], 'v14': r[a]} for a in (*AXES[:3], 'joint_content_scope_citation')}})
    groups = defaultdict(list)
    for r in records:
        groups[r['input_token_ids_sha256']].append(r)
    duplicate_groups = [{'request_ids': [r['request_id'] for r in rows],
                         'identical_content': len({r['content_sha256'] for r in rows}) == 1}
                        for rows in groups.values() if len(rows) > 1]
    inputs['scripts/summarize_structured_study_v14.py'] = sha(__file__)
    return {'schema_version': 'structured_study_summary_v14', 'status': 'completed_descriptive_development',
            'question_count': 8, 'histories': 2, 'native_response_count': 32,
            'conditions': list(CONDITIONS), 'aggregation': aggregation, 'records': records,
            'same_context_reader_contrasts': reader_contrasts,
            'joint_pass_with_incomplete_context_review': [r['request_id'] for r in records
                if r['joint_content_scope_citation'] and r['context_support'] != 'complete'],
            'unique_native_input_token_sequences': len(groups), 'duplicate_input_groups': duplicate_groups,
            'native_main_wall_seconds': receipt['finished_unix_seconds'] - receipt['started_unix_seconds'],
            'input_sha256': dict(sorted(inputs.items())),
            'limits': ['All questions are development material with shared model-assisted grading familiarity.',
                       'Reader checkpoint, quantization, native template and removed no_think suffix change jointly.',
                       'Parent/closure are generic representation baselines over unchanged paired lexical seeds.',
                       'Closure also deduplicates overlapping spans; attachment effects are not separately isolated.',
                       'Equal caps do not mean equal actual tokens or evidence coverage.',
                       'No population accuracy, significance, held-out generalization or novelty claim.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--check', action='store_true')
    a = p.parse_args()
    result = summarize()
    encoded = json.dumps(result, indent=2) + '\n'
    if a.check:
        require(a.output.read_text() == encoded, 'Summary does not replay exactly')
    else:
        with a.output.open('x') as stream:
            stream.write(encoded)
    print(json.dumps({'status': 'passed', 'aggregation': result['aggregation'], 'sha256': sha(a.output)}))


if __name__ == '__main__':
    main()

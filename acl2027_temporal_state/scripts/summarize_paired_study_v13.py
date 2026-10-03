"""Deterministic descriptive joins of frozen native responses and model-assisted grades.

This program does not grade semantics, make model calls or infer generalization.
It fails on missing, duplicate or mismatched cases instead of dropping them.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AXES = ('reference_content_correct', 'scope_correct', 'citation_grounded',
        'format_ok', 'appropriate_abstention', 'native_output_budget_failure')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(batches):
    inputs = {}

    def read(relative):
        path = ROOT / relative
        inputs[relative] = sha(path)
        return json.loads(path.read_text())

    questions = read('data/paired_reader_v13/questions.json')['questions']
    question_map = {q['question_id']: q for q in questions}
    assert len(question_map) == len(questions) == 8
    records = []
    execution = {}
    for batch in batches:
        receipt_name = ('native_paired_reader_v13_receipt.json' if batch == 'main'
                        else 'native_layout_reader_v13_receipt.json')
        receipt = read('results/' + receipt_name)
        assert receipt['status'] == 'completed' and receipt['validated_responses'] == 16
        assert receipt['raw_responses_returned'] == receipt['completion_calls_started'] == 16
        assert receipt['completion_processes'] == 16
        assert receipt['all_inputs_preflight_passed']
        audit_name = ('retrieval_support_audit_v13.json' if batch == 'main'
                      else 'layout_support_audit_v13.json')
        audit = read('results/' + audit_name)
        support = {(r['question_id'], r['condition']): r for r in audit['records']}
        assert len(support) == 16
        seen = set()
        input_tokens, output_tokens = [], []
        frozen = read(f'data/native_paired_reader_v13/{batch}_attempt01/frozen_inputs.json')
        positions = {r: i for i, r in enumerate(frozen['execution_order'])}
        assert len(positions) == 16
        for domain in ('plug', 'opera'):
            refs = read(f'data/paired_reader_v13/references_{domain}.json')
            ref_map = {r['question_id']: r for r in refs['references']}
            manifest = read(f'data/paired_reader_v13/grading_{batch}_{domain}_manifest.json')
            grades = read(f'results/grades_{batch}_{domain}_v13.json')
            assert grades['grading_packet_sha256'] == manifest['packet_sha256']
            mappings = {r['case_id']: r for r in manifest['records']}
            assert len(mappings) == len(grades['cases']) == 8
            assert {c['case_id'] for c in grades['cases']} == set(mappings)
            for case in grades['cases']:
                mapping = mappings[case['case_id']]
                rid = mapping['request_id']
                assert rid not in seen
                seen.add(rid)
                qid, condition = rid.rsplit('__', 1)
                assert condition in ('ordinary', 'paired')
                assert qid == case['question_id'] == mapping['question_id']
                assert case['raw_answer_sha256'] == mapping['content_sha256']
                assert case['received_context_sha256'] == mapping['context_sha256']
                context = support[(qid, condition)]
                assert mapping['context_sha256'] == context['context_sha256']
                idx = positions[rid]
                projection_file = f'data/native_paired_reader_v13/{batch}_attempt01/process_{idx:02}.response_projection.json'
                projection = read(projection_file)
                assert inputs[projection_file] == mapping['response_projection_sha256']
                assert projection['request_id'] == rid
                assert projection['content_sha256'] == case['raw_answer_sha256']
                assert projection['output_budget_failure'] == case['native_output_budget_failure']
                assert not projection['truncated']
                assert projection['tokens_evaluated'] <= 3500
                assert projection['tokens_predicted'] <= 256
                input_tokens.append(projection['tokens_evaluated'])
                output_tokens.append(projection['tokens_predicted'])
                facts = case['required_fact_assessments']
                assert len({f['fact_id'] for f in facts}) == len(facts)
                # Required-fact coverage is checked against each reference's explicit IDs.
                reference_facts = ref_map[qid]['required_facts']
                expected_facts = set(reference_facts) if isinstance(reference_facts, dict) else {f['fact_id'] for f in reference_facts}
                assert {f['fact_id'] for f in facts} == expected_facts
                assert all(f['status'] in ('supported_correct', 'missing', 'incorrect', 'ambiguous') for f in facts)
                assert all(type(case[a]) is bool for a in AXES)
                joint = all(case[a] for a in AXES[:3])
                row = {'batch': batch, 'condition': condition, 'request_id': rid,
                       'question_id': qid, 'history_id': question_map[qid]['history_id'],
                       'case_id': case['case_id'], 'content_sha256': mapping['content_sha256'],
                       'context_sha256': mapping['context_sha256'],
                       'context_support': context['support_status'],
                       **{a: case[a] for a in AXES}, 'joint_content_scope_citation': joint,
                       'fact_status_counts': dict(sorted(Counter(f['status'] for f in facts).items())),
                       'required_fact_count': len(facts)}
                records.append(row)
        assert seen == set(positions)
        execution[batch] = {'responses': 16, 'fresh_completion_processes': 16,
                            'output_budget_failures': receipt['output_budget_failures'],
                            'input_tokens_range': [min(input_tokens), max(input_tokens)],
                            'generated_tokens_range': [min(output_tokens), max(output_tokens)],
                            'wall_seconds': receipt['finished_unix_seconds'] - receipt['started_unix_seconds']}

    aggregate = []
    for batch in batches:
        for condition in ('ordinary', 'paired'):
            rows = [r for r in records if r['batch'] == batch and r['condition'] == condition]
            assert len(rows) == 8
            counts = {a: sum(r[a] for r in rows) for a in (*AXES, 'joint_content_scope_citation')}
            complete = [r for r in rows if r['context_support'] == 'complete']
            aggregate.append({'batch': batch, 'condition': condition, 'questions': 8,
                              **counts, 'context_support_counts': dict(sorted(Counter(r['context_support'] for r in rows).items())),
                              'complete_context_cases': len(complete),
                              'complete_context_joint_passes': sum(r['joint_content_scope_citation'] for r in complete)})

    paired_contrasts = []
    for batch in batches:
        for q in questions:
            cells = {r['condition']: r for r in records if r['batch'] == batch and r['question_id'] == q['question_id']}
            paired_contrasts.append({'batch': batch, 'question_id': q['question_id'],
                                    **{a: {'ordinary': cells['ordinary'][a], 'paired': cells['paired'][a]} for a in AXES[:4]},
                                    'joint_difference_paired_minus_ordinary': int(cells['paired']['joint_content_scope_citation']) - int(cells['ordinary']['joint_content_scope_citation'])})
    layout_contrasts = []
    if len(batches) == 2:
        for condition in ('ordinary', 'paired'):
            for q in questions:
                cells = {r['batch']: r for r in records if r['condition'] == condition and r['question_id'] == q['question_id']}
                layout_contrasts.append({'condition': condition, 'question_id': q['question_id'],
                                        **{a: {'main': cells['main'][a], 'layout': cells['layout'][a]} for a in AXES[:4]},
                                        'joint_difference_layout_minus_main': int(cells['layout']['joint_content_scope_citation']) - int(cells['main']['joint_content_scope_citation'])})
    inputs['scripts/summarize_paired_study_v13.py'] = sha(Path(__file__))
    return {'schema_version': 'paired_study_summary_v0.13', 'batches': batches,
            'input_sha256': dict(sorted(inputs.items())), 'histories': 2, 'questions': 8,
            'native_response_count': len(records), 'aggregation': aggregate, 'execution': execution,
            'records': records, 'paired_contrasts': paired_contrasts, 'layout_contrasts': layout_contrasts,
            'semantics': 'Descriptive fixed-denominator counts over selected development questions. Joint means reference content AND scope AND actual cited-context support. Appropriate abstention can be partial and does not imply completeness or grounding.',
            'limits': ['Model-assisted references and grading, common project familiarity; not human gold or held-out evaluation.',
                       'Two selected histories do not support IID confidence intervals, significance or prevalence claims.',
                       'Metadata verifies recorded joins, not semantic judgments. No model calls occur during aggregation.',
                       'Qwen3-4B pilot reading is not an established strong baseline. Layout changes token counts and formatting, not selected evidence words.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batches', choices=['main', 'both'], default='both')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Verify exact deterministic replay of an existing summary.')
    args = parser.parse_args()
    result = summarize(['main'] if args.batches == 'main' else ['main', 'layout'])
    encoded = json.dumps(result, indent=2) + '\n'
    if args.check:
        assert args.output.read_text() == encoded
    else:
        assert not args.output.exists(), 'Preserve previous summaries; use --check to replay.'
        args.output.write_text(encoded)
    print(json.dumps({'status': 'passed', 'native_response_count': result['native_response_count'],
                      'aggregation': result['aggregation'], 'output_sha256': sha(args.output)}))

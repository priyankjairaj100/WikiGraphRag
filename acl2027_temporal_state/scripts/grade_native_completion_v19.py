"""After-seal offline authored evaluator. Never imported by the prediction host."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re

import native_completion_worker_v19 as worker

ROOT = worker.ROOT
require = worker.require
write = worker.write
digest = worker.digest
read_json = worker.read_json


def verified_seal(directory):
    """Finish every raw-artifact/denominator check before opening attestation/refs."""
    p, c = worker.profile()
    require(not Path(directory).is_symlink(), 'attempt_directory_symlink')
    directory = worker.fixed_directory(directory, p)
    protocol_path = directory / 'frozen_protocol.json'
    require(protocol_path.is_file() and not protocol_path.is_symlink(), 'protocol_not_regular')
    f = read_json(protocol_path)
    require(f['schema_version'] == 'native_completion_frozen_v19' and
            f['profile'] == p and f['native_config'] == c and
            set(f['input_sha256']) == set(worker.INPUT_NAMES), 'frozen_profile_changed')
    public = ROOT / p['public_receipt_path']
    require(f['public_receipt'] == str(public) and not public.is_symlink() and
            public.is_file(), 'fixed_worker_receipt_required')
    protocol_sha = digest(protocol_path)
    receipt = read_json(public)
    require(receipt['raw_outputs_sealed'] is True and receipt['grading_performed'] is False and
            receipt['status'] in ('completed_raw_ungraded', 'failed_raw_ungraded'), 'execution_not_sealed')
    sealpath = directory / 'raw_execution_seal.json'
    require(sealpath.is_file() and not sealpath.is_symlink() and
            digest(sealpath) == receipt['raw_seal_sha256'], 'raw_seal_changed')
    seal = read_json(sealpath)
    require(seal['schema_version'] == 'native_completion_raw_seal_v19' and
            seal['protocol_sha256'] == receipt['protocol_sha256'] == protocol_sha and
            seal['prediction_finished'] is True and seal['grading_performed'] is False and
            seal['all_started_processes_cleanup_confirmed'] is True and
            receipt['all_started_processes_cleanup_confirmed'] is True, 'seal_not_terminal')
    rows = seal['request_states']
    require(rows == receipt['request_states'] and len(rows) == 8 and
            [row['request_id'] for row in rows] == seal['request_ids'] == f['profile']['request_ids'] and
            len(set(seal['request_ids'])) == 8, 'sealed_denominator_changed')
    require(all(row['status'] in ('technical_passed', 'failed', 'not_attempted') for row in rows),
            'nonterminal_request_state')
    require(seal['opaque_reference_sha256'] == f['opaque_reference_sha256'], 'sealed_reference_digest_changed')
    for stage, count in (('process_started', 'model_processes_started'),
                         ('completion_started', 'completion_calls_started'),
                         ('response_received', 'completion_responses')):
        require(all(type(row[stage]) is bool for row in rows) and
                sum(row[stage] for row in rows) == seal[count] == receipt[count] and
                0 <= seal[count] <= 8, 'sealed_counter_mismatch')
    require(seal['completion_responses'] <= seal['completion_calls_started'] <= seal['model_processes_started'],
            'sealed_stage_order_changed')
    artifacts = seal['artifact_sha256']
    require(all('inputs/' + name in artifacts and artifacts['inputs/' + name] == expected
                for name, expected in f['input_sha256'].items()), 'sealed_input_manifest_changed')
    # Check the entire finite-name surface before hashing any raw artifact.
    for name in artifacts:
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and '..' not in relative.parts and len(relative.parts) == 2,
                'unsafe_sealed_path')
        group, filename = relative.parts
        require((group == 'inputs' and filename in worker.INPUT_NAMES) or
                (group in {'completion_%02d' % i for i in range(8)} and
                 (filename in worker.RAW_FIXED_NAMES or
                  re.fullmatch(r'api_(request|response)_[0-9]{4}\.json|api_error_[0-9]{4}\.bin', filename))),
                'sealed_artifact_name_forbidden')
    for name, expected in artifacts.items():
        relative = PurePosixPath(name)
        target = directory / name
        require(all(not (directory.joinpath(*relative.parts[:i])).is_symlink()
                    for i in range(1, len(relative.parts) + 1)) and target.is_file(), 'sealed_artifact_not_regular')
        require(digest(target) == expected, 'sealed_artifact_changed')
    for index, row in enumerate(rows):
        require(not row['response_received'] or row['completion_started'], 'response_without_call')
        require(not row['completion_started'] or row['process_started'], 'call_without_process')
        if row['status'] == 'not_attempted':
            require(not any(row[k] for k in ('process_started', 'completion_started', 'response_received')),
                    'unattempted_row_has_activity')
        technical = row.get('technical_result')
        prefix = 'completion_%02d/' % index
        if technical is not None:
            require(technical['cleanup_confirmed'] is True and
                    all(technical[k] == row[k] for k in ('process_started', 'completion_started', 'response_received')),
                    'sealed_technical_stage_mismatch')
            require(prefix + 'execution_trace.json' in artifacts and
                    artifacts[prefix + 'execution_trace.json'] == technical['execution_trace_sha256'] and
                    prefix + 'technical_result.json' in artifacts and
                    read_json(directory / (prefix + 'technical_result.json')) == technical,
                    'sealed_technical_result_mismatch')
        if row['status'] == 'technical_passed':
            require(technical is not None and technical['status'] == 'technical_passed' and
                    all(row[k] for k in ('process_started', 'completion_started', 'response_received')),
                    'sealed_pass_incomplete')
            require(prefix + 'response.json' in artifacts and
                    artifacts[prefix + 'response.json'] == technical['raw_response_sha256'], 'sealed_response_missing')
    return f, receipt, seal


def grade(directory, public):
    directory = Path(directory).resolve()
    public = Path(public).resolve()
    require(public == ROOT / 'results/native_authored_completion_grade_v19.json', 'fixed_grade_receipt')
    require(not public.exists() and not (directory / 'offline_grade.json').exists(), 'one_offline_grade_no_overwrite')
    f, receipt, seal = verified_seal(directory)
    # This is the first reference-bearing read in this entrypoint, after sealing.
    attestation_path = directory.parent / (directory.name + '_freezer_attestation.json')
    attested = read_json(attestation_path)
    require(worker.runtime.native.base.canonical_hash(attested) == f['freezer_attestation_sha256'],
            'freezer_attestation_changed')
    require(attested['reference_sha256'] == f['opaque_reference_sha256'], 'reference_join_changed')
    for name, expected in attested['privileged_recipe_sha256'].items():
        require(digest(ROOT / name) == expected, 'grading_recipe_changed')
    request_path, reference_path = Path(attested['request_items_path']), Path(attested['reference_path'])
    require(digest(request_path) == attested['request_items_sha256'] and
            digest(reference_path) == attested['reference_sha256'], 'offline_inputs_changed')
    items, references = read_json(request_path), read_json(reference_path)
    require(len(items) == 8 and [x['request_id'] for x in items] == seal['request_ids'] and
            set(references) == {x['fixture_id'] for x in items}, 'offline_denominator_changed')
    rows, details = [], []
    for index in range(8):
        state, item = seal['request_states'][index], items[index]
        row = {'request_id': item['request_id'], 'technical_status': state['status'],
               'output_limit': None, 'authored_control_correct': False,
               'semantic_question_correctness': 'not_checked', 'assessment_performed': False}
        if state['status'] == 'technical_passed':
            response = read_json(directory / ('completion_%02d/response.json' % index))
            prompt = read_json(directory / ('inputs/prompt_%02d.json' % index))
            worker.runtime.native.validate_response(prompt, response, f['native_config'])
            detail, summary = worker.runtime.native.assessment(item, response['content'], references[item['fixture_id']])
            row.update(summary, output_limit=response['stop_type'] == 'limit', assessment_performed=True)
            row['authored_control_correct_before_limit_rule'] = summary['authored_control_correct']
            row['authored_control_correct'] = bool(summary['authored_control_correct'] and not row['output_limit'])
            details.append({'request_id': item['request_id'], 'assessment': detail, 'summary': row})
        rows.append(row)
    passed = all(row['authored_control_correct'] for row in rows)
    result = {'schema_version': 'native_authored_completion_grade_v19',
              'status': 'authored_gate_passed' if passed else 'authored_gate_failed',
              'prediction_protocol_sha256': receipt['protocol_sha256'],
              'raw_execution_seal_sha256': receipt['raw_seal_sha256'],
              'worker_receipt_sha256': digest(Path(f['public_receipt'])),
              'expected_completions': 8, 'technical_passed': sum(x['technical_status'] == 'technical_passed' for x in rows),
              'assessed_outputs': sum(x['assessment_performed'] for x in rows),
              'output_limit_count': sum(x['output_limit'] is True for x in rows),
              'authored_correct_count': sum(x['authored_control_correct'] for x in rows),
              'natural_prompts': 0, 'request_assessments': rows,
              'scope': 'Fictional interface controls only; no natural competence or superiority claim.'}
    write(directory / 'offline_grade.json', {'summary': result, 'details': details}, exclusive=True)
    result['offline_grade_sha256'] = digest(directory / 'offline_grade.json')
    write(public, result, exclusive=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(grade(args.external, args.receipt)))

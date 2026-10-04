"""Reference-free prediction entrypoint. No reference loader or grader imports."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import native_completion_runtime_v19 as runtime

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'configs/native_authored_completion_v19.json'
require = runtime.require
write = runtime.write
digest = runtime.digest
INPUT_NAMES = ('requests.json',) + tuple('prompt_%02d.json' % i for i in range(8))
RAW_FIXED_NAMES = frozenset({'server.log', 'execution_trace.json', 'technical_result.json',
                   'native_prompt.json', 'completion_request.json', 'call_started.json', 'response.json'})
# This is an exact nonrecursive allowlist. Parent freeze recipes, controls,
# references, freezer/grader code, and test fixtures must not be added here.
WORKER_READ_PINS = (
    'scripts/native_completion_worker_v19.py', 'scripts/native_completion_runtime_v19.py',
    'scripts/native_loader_v18_1.py', 'scripts/native_loader_guard_v18.py',
    'scripts/native_binding_reader_v16.py', 'scripts/cgroup_guard_v16_2.py',
    'scripts/process_group_rss_v16.py', 'scripts/local_backend_v07.py',
    'scripts/larger_scorer_v09.py', 'scripts/fetch_local_backend_v07.py',
    'src/temporal_state/reader_binding_v16.py', 'configs/native_authored_completion_v19.json',
    'configs/binding_reader_v16.json', 'configs/model_backend_v09.json',
    'results/reader_acquisition_v16.json', 'results/reader_asset_relocation_v16.json',
)


def read_json(path):
    return json.loads(Path(path).read_text())


def profile():
    p = read_json(PROFILE)
    require(p['schema_version'] == 'native_authored_completion_profile_v19', 'profile_schema')
    require(p['maximum_completions'] == p['maximum_native_processes'] == 8 and
            p['maximum_completions_per_process'] == p['maximum_tokenizations_per_process'] == 1,
            'execution_scope_changed')
    require(p['owned_rss_stop_bytes'] == p['worker_reserve_bytes'] == 4563402752 and
            p['pressure_stop_bytes'] == 7516192768 and p['kernel_memory_max_bytes'] == 8589934592,
            'resource_caps_changed')
    require(p['components'] == list(runtime.pressure.COMPONENTS) and
            p['required_events'] == list(runtime.pressure.EVENTS), 'telemetry_policy_changed')
    require(p['total_boundary_action'] == p['max_event_increase_action'] == 'record_only' and
            p['event_counter_reset_action'] == 'stop' and p['native_log_verbosity'] == 4,
            'guard_or_logging_policy_changed')
    require(p['reference_free_worker'] is True and p['offline_grade_only_after_seal'] is True and
            p['one_attempt_no_retry'] is True and p['natural_prompts_allowed'] is False,
            'research_scope_changed')
    require(len(p['request_ids']) == len(set(p['request_ids'])) == len(p['parent_native_prompt_sha256']) == 8,
            'profile_request_count')
    require(digest(ROOT / 'configs/binding_reader_v16.json') == p['native_config_sha256'], 'native_config_changed')
    c = runtime.native.config(ROOT / 'configs/binding_reader_v16.json')
    return p, c


def fixed_directory(directory, p):
    directory = Path(directory).resolve()
    require(directory == Path(p['attempt_external_path']), 'single_fixed_attempt_destination')
    return directory


def check_inputs(directory, f, p):
    require(set(f['worker_read_pins']) == set(WORKER_READ_PINS), 'worker_read_allowlist_changed')
    for name in WORKER_READ_PINS:
        require(digest(ROOT / name) == f['worker_read_pins'][name], 'worker_code_or_metadata_changed')
    require(set(f['input_sha256']) == set(INPUT_NAMES), 'inference_file_allowlist_changed')
    for name in INPUT_NAMES:
        path = directory / 'inputs' / name
        require(not path.is_symlink() and path.is_file(), 'inference_input_not_regular')
        require(digest(path) == f['input_sha256'][name], 'inference_input_changed')
    require(f['input_sha256']['requests.json'] == p['request_messages_sha256'], 'parent_messages_changed')
    requests = read_json(directory / 'inputs/requests.json')
    require(len(requests) == 8 and [x['request_id'] for x in requests] == p['request_ids'] and
            all(set(x) == {'request_id', 'messages'} for x in requests), 'request_shape_or_order_changed')
    prompts = []
    for index in range(8):
        name = 'prompt_%02d.json' % index
        require(f['input_sha256'][name] == p['parent_native_prompt_sha256'][index], 'parent_prompt_changed')
        prompts.append(read_json(directory / 'inputs' / name))
    require(re.fullmatch(r'[0-9a-f]{64}', f['opaque_reference_sha256']) is not None, 'opaque_digest_shape')
    return requests, prompts


def seal_raw(directory, f, receipt):
    """Commit only known inference/execution artifacts; never enumerate references."""
    require(len(receipt['request_states']) == 8, 'seal_denominator_changed')
    require(receipt['all_started_processes_cleanup_confirmed'], 'seal_requires_confirmed_cleanup')
    artifacts = {'inputs/' + name: digest(directory / 'inputs' / name) for name in INPUT_NAMES}
    for index in range(8):
        folder = directory / ('completion_%02d' % index)
        if not folder.exists():
            continue
        require(not folder.is_symlink(), 'raw_folder_symlink')
        trace_path = folder / 'execution_trace.json'
        require(trace_path.is_file() and not trace_path.is_symlink(), 'missing_terminal_trace')
        trace = read_json(trace_path)
        allowed = set(RAW_FIXED_NAMES)
        for call in trace['api_calls']:
            allowed.add('api_request_%04d.json' % call['index'])
            allowed.add('api_response_%04d.json' % call['index'])
            allowed.add('api_error_%04d.bin' % call['index'])
        for path in sorted(folder.iterdir()):
            require(path.name in allowed and path.is_file() and not path.is_symlink(), 'unexpected_raw_artifact')
            artifacts[str(path.relative_to(directory))] = digest(path)
    seal = {'schema_version': 'native_completion_raw_seal_v19',
            'protocol_sha256': receipt['protocol_sha256'],
            'request_ids': [x['request_id'] for x in receipt['request_states']],
            'request_states': receipt['request_states'],
            'model_processes_started': receipt['model_processes_started'],
            'completion_calls_started': receipt['completion_calls_started'],
            'completion_responses': receipt['completion_responses'],
            'all_started_processes_cleanup_confirmed': True,
            'opaque_reference_sha256': f['opaque_reference_sha256'],
            'artifact_sha256': artifacts, 'prediction_finished': True,
            'grading_performed': False, 'sealed_unix_seconds': time.time()}
    write(directory / 'raw_execution_seal.json', seal, exclusive=True)
    return digest(directory / 'raw_execution_seal.json')


def run(directory, approved_protocol_sha256):
    p, c = profile()
    directory = fixed_directory(directory, p)
    require(digest(directory / 'frozen_protocol.json') == approved_protocol_sha256, 'root_approved_protocol_hash_required')
    f = read_json(directory / 'frozen_protocol.json')
    require(f['schema_version'] == 'native_completion_frozen_v19' and f['profile'] == p and f['native_config'] == c,
            'frozen_profile_changed')
    public = (ROOT / p['public_receipt_path']).resolve()
    require(str(public) == f['public_receipt'], 'public_receipt_path_changed')
    require(not (directory / 'attempt_started.json').exists(), 'one_attempt_no_retry')
    requests, prompts = check_inputs(directory, f, p)
    for prompt in prompts:
        runtime.native.validate_prompt(prompt, c)
    receipt = read_json(public)
    require(receipt['status'] == 'frozen_awaiting_root_execution_approval' and
            receipt['protocol_sha256'] == approved_protocol_sha256, 'attempt_state_changed')
    require(len(receipt['request_states']) == 8 and
            [x['request_id'] for x in receipt['request_states']] == p['request_ids'], 'terminal_denominator_changed')
    write(directory / 'attempt_started.json', {'protocol_sha256': approved_protocol_sha256,
          'started_unix_seconds': time.time()}, exclusive=True)
    receipt.update(status='running_ungraded', all_started_processes_cleanup_confirmed=True)
    write(public, receipt)
    active = None
    event_history = {}
    try:
        for index in range(8):
            active = receipt['request_states'][index]
            check_inputs(directory, f, p)
            require(receipt['all_started_processes_cleanup_confirmed'], 'previous_cleanup_unconfirmed')
            require(receipt['completion_calls_started'] < 8 and receipt['model_processes_started'] < 8,
                    'execution_budget_exhausted')
            active['status'] = 'preparing'
            write(public, receipt)
            def notify(stage, details):
                key = {'process_started': 'model_processes_started',
                       'completion_started': 'completion_calls_started',
                       'response_received': 'completion_responses'}[stage]
                require(not active[stage], 'duplicate_call_accounting')
                active[stage] = True
                active['status'] = stage
                receipt[key] += 1
                write(public, receipt)
            result = runtime.complete_one(c, p, requests[index], prompts[index],
                         directory / ('completion_%02d' % index), notify, event_history)
            active.update(technical_result=result, status=result['status'])
            receipt['all_started_processes_cleanup_confirmed'] &= result['cleanup_confirmed']
            require(all(active[key] == result[key] for key in
                        ('process_started', 'completion_started', 'response_received')),
                    'runtime_callback_accounting_mismatch')
            require(result['status'] == 'technical_passed' and result['cleanup_confirmed'],
                    result.get('failure_code') or 'technical_completion_failed')
            require(all(active[key] for key in ('process_started', 'completion_started', 'response_received')),
                    'passed_completion_missing_stage')
            write(public, receipt)
            active = None
        require(receipt['completion_calls_started'] == receipt['completion_responses'] ==
                receipt['model_processes_started'] == 8, 'incomplete_batch_accounting')
        receipt['status'] = 'completed_raw_ungraded'
    except Exception as error:
        receipt.update(status='failed_raw_ungraded', failure_code=str(error) if isinstance(error, ValueError)
                       else 'worker_technical_error', error_type=type(error).__name__)
        if active is not None:
            active.update(status='failed', failure_code=receipt['failure_code'])
            # Unexpected runtime escape cannot establish process cleanup.
            if active['process_started'] and 'technical_result' not in active:
                receipt['all_started_processes_cleanup_confirmed'] = False
        for row in receipt['request_states']:
            if row['status'] == 'pending':
                row.update(status='not_attempted', reason='earlier_technical_failure')
    finally:
        require(len(receipt['request_states']) == 8, 'final_denominator_changed')
        receipt['finished_unix_seconds'] = time.time()
        receipt['grading_performed'] = False
        if receipt['all_started_processes_cleanup_confirmed']:
            try:
                receipt['raw_seal_sha256'] = seal_raw(directory, f, receipt)
                receipt['raw_outputs_sealed'] = True
            except Exception as error:
                receipt.update(raw_outputs_sealed=False, seal_failure_type=type(error).__name__,
                               status='failed_unsealed')
        else:
            receipt.update(raw_outputs_sealed=False, status='failed_cleanup_unconfirmed')
        write(public, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external', type=Path, required=True)
    parser.add_argument('--approved-protocol-sha256', required=True)
    args = parser.parse_args()
    result = run(args.external, args.approved_protocol_sha256)
    print(json.dumps({k: result[k] for k in ('status', 'model_processes_started',
          'completion_calls_started', 'completion_responses', 'raw_outputs_sealed')}))


if __name__ == '__main__':
    main()

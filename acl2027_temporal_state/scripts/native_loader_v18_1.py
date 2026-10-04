#!/usr/bin/env python3
"""One separately frozen native loader/tokenizer attempt; no generation API."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.request

import native_binding_reader_v16 as native
import native_loader_guard_v18 as pressure
import process_group_rss_v16 as owned

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'configs/native_loader_v18_1.json'
CONTROLS = ROOT / 'data/reader_binding_v16/interface_controls_v16.json'
PINNED_HELPER = 'e3063c8d340877189fc443fb54ae8bc5f7d3388ee37dc16891fda2b401131ee6'
ALLOWED = {('GET', 'health'), ('GET', 'props'), ('POST', 'apply-template'), ('POST', 'tokenize')}
RECIPE = ['scripts/native_loader_v18_1.py', 'scripts/native_loader_v18.py', 'scripts/native_loader_guard_v18.py', 'scripts/native_loader_v17.py', 'scripts/native_binding_reader_v16.py',
          'scripts/cgroup_guard_v16_2.py', 'scripts/process_group_rss_v16.py',
          'scripts/local_backend_v07.py', 'scripts/larger_scorer_v09.py',
          'scripts/fetch_local_backend_v07.py', 'src/temporal_state/reader_binding_v16.py',
          'tests/test_native_loader_v18_1.py', 'tests/test_native_loader_v18.py', 'tests/test_native_loader_v17.py',
          'configs/native_loader_v18_1.json', 'configs/native_loader_v18.json', 'configs/native_loader_v17.json',
          'configs/binding_reader_v16.json', 'data/reader_binding_v16/interface_controls_v16.json',
          'results/owned_rss_audit_closure_v17.json', 'results/reader_native_capacity_estimate_v17.json',
          'docs/native_reader_resource_proposal_v17.txt', 'docs/native_loader_guard_amendment_v18.txt',
          'results/native_loader_attempt_v17.json', 'results/native_loader_attempt_v17_1.json',
          'results/native_loader_terminal_review_v17_1.json',
          'results/native_loader_guard_controls_v18.json',
          'results/native_loader_independent_review_v18.json',
          'results/native_loader_attempt_v18.json', 'results/native_loader_terminal_review_v18.json',
          'results/native_rollback_observation_diagnosis_v18.json',
          'docs/native_rollback_observation_diagnosis_v18.txt',
          'docs/native_loader_logging_amendment_v18_1.txt',
          'results/native_loader_logging_controls_v18_1.json',
          'results/native_loader_independent_review_v18_1.json']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def write(path, obj, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w') as stream:
        json.dump(obj, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def profile():
    p = load(PROFILE)
    require(p['schema_version'] == 'native_loader_profile_v18_1', 'profile_schema')
    require(p['owned_rss_stop_bytes'] == p['worker_reserve_bytes'] == 4563402752, 'rss_reserve_changed')
    require(p['pressure_stop_bytes'] == 7516192768 and p['kernel_memory_max_bytes'] == 8589934592,
            'shared_pressure_policy_changed')
    require(p['maximum_tokenizations'] == 8 and p['maximum_completions'] == 0 and
            p['maximum_native_processes'] == 1, 'execution_scope_changed')
    require(p['components'] == list(pressure.COMPONENTS), 'pressure_components_changed')
    require(digest(ROOT / 'scripts/process_group_rss_v16.py') == PINNED_HELPER, 'rss_helper_changed')
    require(p['total_boundary_action'] == p['max_event_increase_action'] == 'record_only', 'diagnostic_policy_changed')
    require(p['required_events'] == list(pressure.EVENTS), 'required_events_changed')
    require(p['native_log_verbosity'] == 4, 'logging_observation_recipe_changed')
    require(p['event_counter_reset_action'] == 'stop' and p['one_attempt_no_retry'] is True, 'failure_policy_changed')
    c = native.config(ROOT / 'configs/binding_reader_v16.json')
    require(digest(ROOT / 'configs/binding_reader_v16.json') == p['native_config_sha256'], 'native_config_changed')
    return p, c


def preflight_reason(snapshot, baseline=None, reserve=0):
    return pressure.policy_reason(snapshot, baseline, reserve)


def owned_reason(snapshot, *, live, rss_limit):
    if not snapshot.get('telemetry_ok'):
        return 'owned_rss_telemetry_unavailable'
    if live and (not snapshot.get('group_members_observed') or snapshot.get('rss_bytes', 0) <= 0):
        return 'live_owned_group_unobserved'
    if snapshot.get('rss_bytes', 0) >= rss_limit:
        return 'owned_group_rss_limit'
    return None


def freeze(directory, receipt):
    p, c = profile()
    directory = native.external(directory)
    receipt = Path(receipt).resolve()
    require(directory == Path(p['attempt_external_path']) and
            receipt == (ROOT / p['public_receipt_path']).resolve(), 'single_fixed_attempt_destination')
    require(receipt.is_relative_to(ROOT / 'results'), 'public_receipt_location')
    require(not directory.exists() and not receipt.exists(), 'freeze_destination_exists')
    require(not Path(c['model']['path']).is_symlink() and
            Path(c['model']['path']).stat().st_size == c['model']['bytes'], 'model_metadata_changed')
    items = native.authored_requests(load(CONTROLS))
    requests = [{'request_id': x['request_id'], 'messages': native.request_messages(x)} for x in items]
    require(len(requests) == 8, 'full_request_count_required')
    directory.mkdir(parents=True)
    write(directory / 'requests.json', requests, exclusive=True)
    require(digest(directory / 'requests.json') == p['unchanged_authored_requests_sha256'], 'authored_requests_changed')
    frozen = {'schema_version': 'native_loader_frozen_v18_1', 'profile': p,
              'native_config': c, 'request_count': 8, 'public_receipt': str(receipt),
              'requests_sha256': digest(directory / 'requests.json'),
              'recipe_sha256': {x: digest(ROOT / x) for x in RECIPE},
              'model_digest_recheck_required_before_popen': True,
              'references_copied': False, 'natural_requests_allowed': False,
              'maximum_completions': 0, 'maximum_native_processes': 1}
    write(directory / 'frozen_protocol.json', frozen, exclusive=True)
    r = {'schema_version': 'native_loader_receipt_v18_1', 'status': 'frozen_awaiting_root_execution_approval',
         'protocol_sha256': digest(directory / 'frozen_protocol.json'),
         'model_sha256': c['model']['sha256'], 'model_processes_started': 0,
         'completion_calls_started': 0, 'completion_responses': 0,
         'natural_prompts': 0, 'expected_authored_tokenizations': 8, 'tokenized_prompts': 0,
         'request_states': [{'request_id': x['request_id'], 'status': 'not_attempted'} for x in requests]}
    write(receipt, r, exclusive=True)
    return r


def api(port, method, endpoint, payload, folder, calls, timeout=30):
    require((method, endpoint) in ALLOWED, 'endpoint_forbidden')
    if endpoint == 'apply-template':
        require(set(payload) == {'messages'}, 'template_payload_shape')
    if endpoint == 'tokenize':
        require(set(payload) == {'content', 'add_special', 'parse_special'} and
                payload['add_special'] is True and payload['parse_special'] is True, 'tokenizer_payload_shape')
    call = {'index': len(calls), 'method': method, 'endpoint': endpoint, 'started': time.time()}
    calls.append(call)
    write(folder / ('api_request_%04d.json' % call['index']), {'method': method, 'endpoint': endpoint, 'payload': payload})
    request = urllib.request.Request(f'http://127.0.0.1:{port}/{endpoint}',
               data=None if payload is None else json.dumps(payload).encode(),
               headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(16 * 2**20 + 1)
        require(len(body) <= 16 * 2**20, 'http_response_over_budget')
        target = folder / ('api_response_%04d.json' % call['index'])
        target.write_bytes(body)
        call.update(response_sha256=digest(target), status='received')
        return json.loads(body)
    except Exception as error:
        call.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        call['finished'] = time.time()


def signal_inner(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def watchdog(process, check, stopped, failure, interval):
    """A blocked HTTP call cannot delay a guard-triggered group stop."""
    while not stopped.wait(interval):
        try:
            check()
        except Exception as error:
            failure.append(str(error) if isinstance(error, ValueError) else type(error).__name__)
            signal_inner(process, signal.SIGKILL)
            return


def cleanup(process, anchor):
    record = {'signal_target_inner_pgid': process.pid, 'confirmed': False}
    if not anchor or not anchor.get('telemetry_ok'):
        if process.poll() is None:
            signal_inner(process, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                signal_inner(process, signal.SIGKILL)
                process.wait(timeout=5)
        record['reason'] = 'ownership_anchor_unavailable_cleanup_unconfirmed'
        return record
    signal_inner(process, signal.SIGTERM)
    for stage, seconds in [('term', 10), ('kill', 5)]:
        if stage == 'kill':
            signal_inner(process, signal.SIGKILL)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            process.poll()
            snap = owned.read_group_rss(process.pid, anchor=anchor)
            record['last_snapshot'] = snap
            if not snap.get('telemetry_ok'):
                record['reason'] = 'cleanup_telemetry_unavailable'
                break
            live = [m for m in snap['members'] if m['state'] not in ('Z', 'X', 'x')]
            if process.poll() is not None and not live:
                record.update(confirmed=True, process_returncode=process.returncode)
                return record
            time.sleep(0.1)
    record['reason'] = 'owned_cleanup_unconfirmed'
    return record


def command(c):
    return native.command(c) + ['--log-verbosity', '4']


def run(directory, approval_sha256):
    directory = native.external(directory)
    f = load(directory / 'frozen_protocol.json')
    require(digest(directory / 'frozen_protocol.json') == approval_sha256, 'approved_protocol_hash_required')
    require(not (directory / 'attempt_started.json').exists(), 'one_attempt_no_retry')
    for name, expected in f['recipe_sha256'].items():
        require(digest(ROOT / name) == expected, 'frozen_recipe_changed')
    require(digest(directory / 'requests.json') == f['requests_sha256'], 'frozen_requests_changed')
    p, c = profile()
    require(p == f['profile'] and c == f['native_config'], 'frozen_profile_changed')
    require(directory == Path(p['attempt_external_path']) and
            Path(f['public_receipt']).resolve() == (ROOT / p['public_receipt_path']).resolve(), 'single_fixed_attempt_destination')
    requests = load(directory / 'requests.json')
    r = load(f['public_receipt'])
    require(len(requests) == len(r['request_states']) == f['request_count'] == 8, 'full_request_count_required')
    require([x['request_id'] for x in requests] == [x['request_id'] for x in r['request_states']], 'request_identity_changed')
    require(r['status'] == 'frozen_awaiting_root_execution_approval', 'attempt_state_changed')
    write(directory / 'attempt_started.json', {'approved_protocol_sha256': approval_sha256, 'started': time.time()}, exclusive=True)
    trace = {'api_calls': [], 'resource_samples': [], 'completion_calls': 0,
             'total_boundary_and_max_increases_are_diagnostic': True}
    sampling_lock = threading.Lock()
    process = None
    anchor = None
    watcher = None
    lock = None
    log = None
    stopped = threading.Event()
    failure = []
    current_row = None
    r['status'] = 'attempt_started'
    write(f['public_receipt'], r)
    try:
        baseline = pressure.read_snapshot()
        trace['pre_asset_snapshot'] = baseline
        reason = preflight_reason(baseline, reserve=p['worker_reserve_bytes'])
        require(reason is None, reason or 'pre_asset_resource_refusal')
        trace['verified_assets'] = native.verify_assets(c)
        baseline_after = pressure.read_snapshot()
        trace['prelaunch_snapshot'] = baseline_after
        reason = preflight_reason(baseline_after, baseline, p['worker_reserve_bytes'])
        require(reason is None, reason or 'prelaunch_resource_refusal')
        baseline = baseline_after
        lock = (Path(c['model']['path']).parent / 'native_binding_reader_v16.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with socket.socket() as port:
            port.bind(('127.0.0.1', c['port']))
        require(os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize >= 256 * 2**20, 'audit_space_reserve')
        def limits():
            resource.setrlimit(resource.RLIMIT_AS, (c['address_space_limit_bytes'],) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (c['cpu_seconds_limit'],) * 2)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        started = time.monotonic()
        log = (directory / 'server.log').open('xb')
        process = subprocess.Popen(command(c), stdout=log, stderr=subprocess.STDOUT,
                  env=dict(os.environ, LD_LIBRARY_PATH=c['runtime']['directory'], OMP_NUM_THREADS=str(c['threads'])),
                  start_new_session=True, preexec_fn=limits)
        r['model_processes_started'] = 1
        write(f['public_receipt'], r)
        trace['inner_process_id'] = process.pid
        anchor = owned.bind_group(process.pid)
        trace['ownership_anchor'] = anchor
        require(anchor.get('telemetry_ok'), 'ownership_anchor_failed')
        def check_sample():
            nonlocal baseline
            snap = pressure.read_snapshot()
            group = owned.read_group_rss(process.pid, anchor=anchor)
            live = process.poll() is None
            trace['resource_samples'].append({'cgroup': snap, 'owned_group': group, 'process_live_after_scan': live})
            require(len(trace['resource_samples']) <= 6000, 'trace_sample_cap')
            reason = preflight_reason(snap, baseline)
            require(reason is None, reason or 'resource_refusal')
            baseline = snap  # Compare the next sample to this one, not just initial events.
            reason = owned_reason(group, live=live, rss_limit=p['owned_rss_stop_bytes'])
            require(reason is None, reason or 'owned_resource_refusal')
            require(live, 'native_process_exited')
            require(time.monotonic() - started < c['process_wall_seconds'], 'process_wall_limit')
            require((directory / 'server.log').stat().st_size < 64 * 2**20, 'native_log_budget')
        def check():
            # Serialize event lineage only; no HTTP or asset work holds this lock.
            with sampling_lock:
                check_sample()
        check()
        watcher = threading.Thread(target=watchdog,
                  args=(process, check, stopped, failure, c['monitor_interval_seconds']), daemon=True)
        watcher.start()
        while True:
            require(not failure, failure[0] if failure else 'watchdog_failed')
            require(process.poll() is None, 'native_process_exited_during_startup')
            require(time.monotonic() - started < c['startup_timeout_seconds'], 'startup_timeout')
            try:
                health = api(c['port'], 'GET', 'health', None, directory, trace['api_calls'], timeout=1)
                if health.get('status') == 'ok':
                    break
            except OSError:
                pass
            time.sleep(0.1)
        props = api(c['port'], 'GET', 'props', None, directory, trace['api_calls'])
        require(props.get('model_path') == c['model']['path'] and props.get('total_slots') == 1,
                'native_identity_or_slot_mismatch')
        require(props['default_generation_settings']['n_ctx'] == 8192 and props['build_info'] == 'b11146-7fe450e19',
                'native_context_or_build_mismatch')
        r['health_identity_passed'] = True
        startup_log = (directory / 'server.log').read_text(errors='replace')
        rollback = re.findall(r'\bn_rs_seq\s*=\s*(\d+)\b', startup_log)
        require(rollback and all(int(x) == 0 for x in rollback), 'native_rollback_state_not_zero_or_unobserved')
        trace['native_buffer_report_lines'] = [line for line in startup_log.splitlines()
             if 'buffer size' in line or ('size =' in line and ('cache' in line or 'memory_recurrent' in line))]
        trace['native_n_rs_seq_values'] = [int(x) for x in rollback]
        for i in range(8):
            check()
            require(not failure, failure[0] if failure else 'watchdog_failed')
            current_row = r['request_states'][i]
            current_row['status'] = 'rendering'
            rendered = api(c['port'], 'POST', 'apply-template', {'messages': requests[i]['messages']}, directory, trace['api_calls'], timeout=c['per_request_timeout_seconds'])
            tokens = api(c['port'], 'POST', 'tokenize', {'content': rendered['prompt'], 'add_special': True,
                         'parse_special': True}, directory, trace['api_calls'], timeout=c['per_request_timeout_seconds'])['tokens']
            prompt = {'rendered_prompt': rendered['prompt'], 'input_token_ids': tokens,
                      'rendered_prompt_sha256': hashlib.sha256(rendered['prompt'].encode()).hexdigest(),
                      'input_token_ids_sha256': native.base.canonical_hash(tokens), 'input_tokens': len(tokens)}
            target = directory / ('native_prompt_%02d.json' % i)
            write(target, prompt, exclusive=True)
            native.validate_prompt(prompt, c)
            current_row.update(status='tokenized', input_tokens=len(tokens), prompt_artifact_sha256=digest(target))
            r['tokenized_prompts'] += 1
            write(f['public_receipt'], r)
            current_row = None
        check()
        require(not failure and r['tokenized_prompts'] == 8, 'tokenizer_gate_incomplete')
        r['status'] = 'loader_tokenizer_passed_pending_cleanup'
    except Exception as error:
        r.update(status='failed', error_type=type(error).__name__, failure_code=str(error) if isinstance(error, ValueError) else 'native_loader_error')
        if process is not None:
            signal_inner(process, signal.SIGKILL)
            trace['exception_stop_signal'] = 'SIGKILL_original_inner_group'
        if current_row is not None:
            current_row.update(status='failed', reason=r['failure_code'])
        for row in r['request_states']:
            if row['status'] == 'not_attempted':
                row['reason'] = r['failure_code']
    finally:
        stopped.set()
        if watcher is not None:
            watcher.join(timeout=2)
            if watcher.is_alive():
                failure.append('watchdog_join_timeout')
        if failure:
            r.update(status='failed', failure_code=failure[0])
        if process is not None:
            try:
                trace['cleanup'] = cleanup(process, anchor)
            except Exception as error:
                trace['cleanup'] = {'confirmed': False, 'reason': 'cleanup_exception',
                                    'error_type': type(error).__name__}
            if not trace['cleanup']['confirmed']:
                r.update(status='failed', failure_code='owned_cleanup_unconfirmed')
        else:
            trace['cleanup'] = {'confirmed': True, 'process_never_started': True}
        if log is not None:
            log.close()
        if lock is not None:
            lock.close()
        if r['status'] == 'loader_tokenizer_passed_pending_cleanup':
            r['status'] = 'loader_tokenizer_passed'
        trace['finished_unix_seconds'] = time.time()
        write(directory / 'execution_trace.json', trace, exclusive=True)
        r.update(execution_trace_sha256=digest(directory / 'execution_trace.json'),
                 cleanup_confirmed=trace['cleanup']['confirmed'],
                 api_endpoint_counts={name: sum(x['endpoint'] == name for x in trace['api_calls']) for name in ['health', 'props', 'apply-template', 'tokenize']},
                 natural_QA_or_generation_claim=False)
        require(len(r['request_states']) == 8, 'final_denominator_changed')
        write(f['public_receipt'], r)
    return r


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['freeze', 'run'])
    parser.add_argument('--external', type=Path, required=True)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--approved-protocol-sha256')
    args = parser.parse_args()
    if args.mode == 'freeze':
        require(args.receipt is not None, 'receipt_required')
        result = freeze(args.external, args.receipt)
    else:
        require(bool(args.approved_protocol_sha256), 'root_approved_protocol_hash_required')
        result = run(args.external, args.approved_protocol_sha256)
    print(json.dumps({'status': result['status'], 'model_processes_started': result['model_processes_started'],
                      'tokenized_prompts': result['tokenized_prompts'], 'completion_calls_started': 0}))


if __name__ == '__main__':
    main()

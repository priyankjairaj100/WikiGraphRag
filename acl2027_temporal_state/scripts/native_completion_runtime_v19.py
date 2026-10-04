"""One reference-free cold native completion with the reviewed v18.1 lifecycle.

The inherited modules have no top-level reference reads. Only their pure native
configuration, asset, token/accounting and process helpers are used here; no
assessment, freeze, execute, check_frozen or grading function is called.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import native_loader_v18_1 as ancestor

native = ancestor.native
pressure = ancestor.pressure
owned = ancestor.owned
require = ancestor.require
write = ancestor.write
digest = ancestor.digest
command = ancestor.command
preflight_reason = ancestor.preflight_reason
owned_reason = ancestor.owned_reason
signal_inner = ancestor.signal_inner
watchdog = ancestor.watchdog
cleanup = ancestor.cleanup
PARAMETERS = dict(native.PARAMETERS)
ALLOWED = ancestor.ALLOWED | {('POST', 'completion')}


def api(port, method, endpoint, payload, folder, calls, timeout=30):
    if endpoint != 'completion':
        return ancestor.api(port, method, endpoint, payload, folder, calls, timeout)
    require((method, endpoint) == ('POST', 'completion'), 'endpoint_forbidden')
    require(set(payload) == set(PARAMETERS) | {'prompt'} and
            all(payload[key] == value for key, value in PARAMETERS.items()) and
            isinstance(payload['prompt'], list) and payload['prompt'] and
            all(type(x) is int and x >= 0 for x in payload['prompt']), 'completion_payload_changed')
    call = {'index': len(calls), 'method': method, 'endpoint': endpoint, 'started': time.time()}
    calls.append(call)
    write(folder / ('api_request_%04d.json' % call['index']),
          {'method': method, 'endpoint': endpoint, 'payload': payload}, exclusive=True)
    request = urllib.request.Request(f'http://127.0.0.1:{port}/completion',
              data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
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
        if isinstance(error, urllib.error.HTTPError):
            raw = error.read(16 * 2**20 + 1)
            target = folder / ('api_error_%04d.bin' % call['index'])
            target.write_bytes(raw)
            call['error_body_sha256'] = digest(target)
        raise
    finally:
        call['finished'] = time.time()


def complete_one(c, profile, item, original_prompt, folder, notify, event_history):
    """Technical-only completion; answer text is never parsed or graded."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    trace = {'api_calls': [], 'resource_samples': [], 'completion_calls': 0,
             'command': command(c), 'request_id': item['request_id']}
    result = {'status': 'failed', 'failure_code': 'not_started', 'process_started': False,
              'completion_started': False, 'response_received': False}
    process = None
    anchor = None
    watcher = None
    lock = None
    log = None
    failure = []
    stopped = threading.Event()
    sampling_lock = threading.Lock()
    try:
        baseline = pressure.read_snapshot()
        trace['pre_asset_snapshot'] = baseline
        trace['previous_attempt_snapshot'] = event_history.get('last')
        reason = preflight_reason(baseline, event_history.get('last'), profile['worker_reserve_bytes'])
        require(reason is None, reason or 'pre_asset_resource_refusal')
        event_history['last'] = baseline
        trace['verified_assets'] = native.verify_assets(c)
        baseline_after = pressure.read_snapshot()
        trace['prelaunch_snapshot'] = baseline_after
        reason = preflight_reason(baseline_after, baseline, profile['worker_reserve_bytes'])
        require(reason is None, reason or 'prelaunch_resource_refusal')
        baseline = baseline_after
        event_history['last'] = baseline
        lock = (Path(c['model']['path']).parent / 'native_binding_reader_v16.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with socket.socket() as port:
            port.bind(('127.0.0.1', c['port']))
        fs = os.statvfs(folder)
        require(fs.f_bavail * fs.f_frsize >= 256 * 2**20, 'audit_space_reserve')
        def limits():
            resource.setrlimit(resource.RLIMIT_AS, (c['address_space_limit_bytes'],) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (c['cpu_seconds_limit'],) * 2)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        started = time.monotonic()
        log = (folder / 'server.log').open('xb')
        process = subprocess.Popen(command(c), stdout=log, stderr=subprocess.STDOUT,
                  env=dict(os.environ, LD_LIBRARY_PATH=c['runtime']['directory'], OMP_NUM_THREADS=str(c['threads'])),
                  start_new_session=True, preexec_fn=limits)
        result['process_started'] = True
        notify('process_started', {})
        trace['inner_process_id'] = process.pid
        anchor = owned.bind_group(process.pid)
        trace['ownership_anchor'] = anchor
        require(anchor.get('telemetry_ok'), 'ownership_anchor_failed')
        def check_sample():
            nonlocal baseline
            snap = pressure.read_snapshot()
            group = owned.read_group_rss(process.pid, anchor=anchor)
            live = process.poll() is None
            trace['resource_samples'].append({'cgroup': snap, 'owned_group': group,
                                              'process_live_after_scan': live})
            require(len(trace['resource_samples']) <= 6000, 'trace_sample_cap')
            reason = preflight_reason(snap, baseline)
            require(reason is None, reason or 'resource_refusal')
            baseline = snap
            event_history['last'] = snap
            reason = owned_reason(group, live=live, rss_limit=profile['owned_rss_stop_bytes'])
            require(reason is None, reason or 'owned_resource_refusal')
            require(live, 'native_process_exited')
            require(time.monotonic() - started < c['process_wall_seconds'], 'process_wall_limit')
            require((folder / 'server.log').stat().st_size < 64 * 2**20, 'native_log_budget')
        def check():
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
                health = api(c['port'], 'GET', 'health', None, folder, trace['api_calls'], timeout=1)
                if health.get('status') == 'ok':
                    break
            except OSError:
                pass
            time.sleep(0.1)
        props = api(c['port'], 'GET', 'props', None, folder, trace['api_calls'])
        require(props.get('model_path') == c['model']['path'] and props.get('total_slots') == 1,
                'native_identity_or_slot_mismatch')
        require(props['default_generation_settings']['n_ctx'] == 8192 and props['build_info'] == 'b11146-7fe450e19',
                'native_context_or_build_mismatch')
        trace['health_identity_passed'] = True
        startup_log = (folder / 'server.log').read_text(errors='replace')
        rollback = re.findall(r'\bn_rs_seq\s*=\s*(\d+)\b', startup_log)
        require(rollback and all(int(x) == 0 for x in rollback), 'native_rollback_state_not_zero_or_unobserved')
        trace['native_n_rs_seq_values'] = [int(x) for x in rollback]
        trace['native_buffer_report_lines'] = [line for line in startup_log.splitlines()
             if 'buffer size' in line or ('size =' in line and ('cache' in line or 'memory_recurrent' in line))]
        check()
        rendered = api(c['port'], 'POST', 'apply-template', {'messages': item['messages']},
                       folder, trace['api_calls'], timeout=c['per_request_timeout_seconds'])
        tokens = api(c['port'], 'POST', 'tokenize', {'content': rendered['prompt'], 'add_special': True,
                     'parse_special': True}, folder, trace['api_calls'], timeout=c['per_request_timeout_seconds'])['tokens']
        prompt = {'rendered_prompt': rendered['prompt'], 'input_token_ids': tokens,
                  'rendered_prompt_sha256': hashlib.sha256(rendered['prompt'].encode()).hexdigest(),
                  'input_token_ids_sha256': native.base.canonical_hash(tokens), 'input_tokens': len(tokens)}
        write(folder / 'native_prompt.json', prompt, exclusive=True)
        native.validate_prompt(prompt, c)
        require(prompt == original_prompt, 'native_prompt_changed')
        check()
        require(not failure, failure[0] if failure else 'watchdog_failed')
        request = PARAMETERS | {'prompt': prompt['input_token_ids']}
        write(folder / 'completion_request.json', request, exclusive=True)
        write(folder / 'call_started.json', {'request_id': item['request_id'], 'started': time.time(),
              'request_sha256': native.base.canonical_hash(request)}, exclusive=True)
        result['completion_started'] = True
        trace['completion_calls'] = 1
        notify('completion_started', {})
        response = api(c['port'], 'POST', 'completion', request, folder, trace['api_calls'],
                       timeout=c['per_request_timeout_seconds'])
        write(folder / 'response.json', response, exclusive=True)
        result['response_received'] = True
        notify('response_received', {})
        native.validate_response(prompt, response, c)
        check()
        require(not failure, failure[0] if failure else 'watchdog_failed')
        result.update(status='technical_passed', failure_code=None,
                      input_tokens=prompt['input_tokens'], output_tokens=response['tokens_predicted'],
                      output_limit=response['stop_type'] == 'limit',
                      raw_response_sha256=digest(folder / 'response.json'))
    except Exception as error:
        result.update(status='failed', failure_code=str(error) if isinstance(error, ValueError) else 'native_completion_error',
                      error_type=type(error).__name__)
        if process is not None:
            signal_inner(process, signal.SIGKILL)
            trace['exception_stop_signal'] = 'SIGKILL_original_inner_group'
    finally:
        stopped.set()
        if watcher is not None:
            watcher.join(timeout=2)
            if watcher.is_alive():
                failure.append('watchdog_join_timeout')
        if failure:
            result.update(status='failed', failure_code=failure[0])
        if process is not None:
            try:
                trace['cleanup'] = cleanup(process, anchor)
            except Exception as error:
                trace['cleanup'] = {'confirmed': False, 'reason': 'cleanup_exception',
                                    'error_type': type(error).__name__}
        else:
            trace['cleanup'] = {'confirmed': True, 'process_never_started': True}
        if not trace['cleanup']['confirmed']:
            result.update(status='failed', failure_code='owned_cleanup_unconfirmed')
        elif event_history.get('last') is not None:
            # New batch boundary: do not reset counter history for fresh models.
            post_cleanup = pressure.read_snapshot()
            trace['post_cleanup_snapshot'] = post_cleanup
            reason = preflight_reason(post_cleanup, event_history['last'])
            if reason is not None:
                result.update(status='failed', failure_code=reason)
            else:
                event_history['last'] = post_cleanup
        if log is not None:
            log.close()
        if lock is not None:
            lock.close()
        trace['finished_unix_seconds'] = time.time()
        write(folder / 'execution_trace.json', trace, exclusive=True)
        result.update(cleanup_confirmed=trace['cleanup']['confirmed'],
                      execution_trace_sha256=digest(folder / 'execution_trace.json'),
                      api_endpoint_counts={name: sum(x['endpoint'] == name for x in trace['api_calls'])
                         for name in ('health', 'props', 'apply-template', 'tokenize', 'completion')})
        write(folder / 'technical_result.json', result, exclusive=True)
    return result

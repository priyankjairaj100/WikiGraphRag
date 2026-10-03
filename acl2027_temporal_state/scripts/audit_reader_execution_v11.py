#!/usr/bin/env python3
"""Independent offline audit of v11 native attempt provenance, never inference.

Only standard-library reads/hashing are used. Native responses are not opened
until the final receipt certifies that both completions finished. This audits
execution provenance and exact inputs, not answer or scientific correctness.
"""
import argparse
from datetime import datetime
import difflib
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(Path(path).read_text())


def digest(path):
    h = sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def text_hash(value):
    return sha256(value.encode()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def manifest_check(receipt, external, project_data):
    require(external.is_dir(), 'missing external attempt directory')
    require(set(receipt['external_artifacts']) == {
        str(p.relative_to(external)) for p in external.rglob('*') if p.is_file()},
        'external artifact coverage differs')
    for name, spec in receipt['external_artifacts'].items():
        p = external / name
        require(p.stat().st_size == spec['bytes'] and digest(p) == spec['sha256'],
                'external artifact byte/hash mismatch: ' + name)
    require(set(receipt['project_artifacts']) == {
        str(p.relative_to(ROOT)) for p in project_data.rglob('*') if p.is_file()},
        'project attempt artifact coverage differs')
    for name, expected in receipt['project_artifacts'].items():
        require(digest(ROOT / name) == expected, 'project artifact hash mismatch: ' + name)
    return {'external_artifacts': len(receipt['external_artifacts']),
            'project_artifacts': len(receipt['project_artifacts']),
            'external_bytes': sum(x['bytes'] for x in receipt['external_artifacts'].values())}


def audit(external01, external02, assets):
    r1p = ROOT / 'results/native_reader_v11_receipt.json'
    r2p = ROOT / 'results/native_reader_attempt02_v11_receipt.json'
    r1, r2 = load(r1p), load(r2p)
    require(r2['status'] == 'completed' and r2['raw_responses_returned'] ==
            r2['validated_responses'] == 2, 'both native responses must be complete before audit')
    d1, d2 = (ROOT / 'data/native_reader_v11' / x for x in ('attempt01', 'attempt02'))
    manifests = {'attempt01': manifest_check(r1, external01, d1),
                 'attempt02': manifest_check(r2, external02, d2)}
    f1, f2 = load(d1 / 'frozen_inputs.json'), load(d2 / 'frozen_inputs.json')
    cp = ROOT / 'configs/native_reader_v11.json'
    bp = ROOT / 'configs/model_backend_v09.json'
    c, b = load(cp), load(bp)
    for r, f, d, e in ((r1, f1, d1, external01), (r2, f2, d2, external02)):
        require(r['frozen_inputs_sha256'] == digest(d / 'frozen_inputs.json') ==
                digest(e / 'frozen_inputs.json'), 'freeze copies mismatch')
        require(f['completion_calls_at_freeze'] == 0 and f['config'] == c, 'freeze/config mismatch')
        for name, expected in f['source_hashes'].items():
            p = Path(name) if Path(name).is_absolute() else ROOT / name
            require(digest(p) == expected, 'frozen executable/configuration changed: ' + name)
        require(digest(e / 'request.json') == f['request_sha256'] and
                (e / 'request.json').stat().st_size == f['request_bytes'], 'request binding mismatch')
        require(digest(e / 'protocol.snapshot') == f['protocol_sha256'], 'protocol snapshot mismatch')
        require(digest(e / 'config.snapshot.json') == digest(cp), 'configuration snapshot mismatch')
    require(c['backend_config_sha256'] == digest(bp), 'backend configuration binding mismatch')
    require(r1['status'] == 'failed' and r1['preflight_processes'] == 1 and
            r1['all_inputs_preflight_passed'] is False, 'attempt01 failure state differs')
    require(all(r1[k] == 0 for k in ('completion_processes', 'completion_calls_started',
            'raw_responses_returned', 'validated_responses')), 'attempt01 inference occurred')
    t1 = load(external01 / 'preflight.execution.json')
    require('process_id' not in t1 and t1['cgroup_memory_before_bytes'] is None,
            'attempt01 native launch not excluded')
    require(load(external01 / 'rendered_prompts.json') == [], 'attempt01 prompt was tokenized')
    require(load(external01 / 'failure.json') == {'type': 'RuntimeError',
            'message': 'required cgroup memory telemetry is unavailable'}, 'attempt01 reason differs')
    require(not any('response' in n or n.endswith('.server.log') for n in
            r1['external_artifacts']), 'attempt01 unexpected output or server log')

    ap = ROOT / 'data/correction_gate_v11/reader_execution_amendment_v11.json'
    pp = ROOT / 'data/correction_gate_v11/reader_protocol.json'
    a, p = load(ap), load(pp)
    require(a['parent_protocol'] == p and a['parent_protocol_sha256'] == digest(pp),
            'amendment parent mismatch')
    require(a['prior_attempt_receipt_sha256'] == digest(r1p) and
            a['prior_attempt_launched_model_processes'] == a['prior_attempt_completion_calls'] ==
            a['completion_outputs_at_amendment'] == 0, 'amendment failure binding mismatch')
    s1p, s2p = ROOT / 'scripts/native_reader_v11.py', ROOT / 'scripts/native_reader_attempt02_v11.py'
    s1, s2 = s1p.read_text(), s2p.read_text()
    expected = s1.replace("DATA = PROJECT / 'data/native_reader_v11/attempt01'",
                          "DATA = PROJECT / 'data/native_reader_v11/attempt02'").replace(
        "RECEIPT = PROJECT / 'results/native_reader_v11_receipt.json'",
        "RECEIPT = PROJECT / 'results/native_reader_attempt02_v11_receipt.json'")
    require(s2 == expected and s1 != s2, 'collector changed beyond DATA/RECEIPT')
    diff = list(difflib.unified_diff(s1.splitlines(), s2.splitlines(),
        fromfile='native_reader_v11.py', tofile='native_reader_attempt02_v11.py', lineterm=''))
    require(diff == a['collector_diff'], 'recorded collector diff differs')
    require(digest(s1p) == a['collector_original_sha256'] == digest(external01 / 'collector.snapshot.py')
            and digest(s2p) == a['collector_attempt02_sha256'] == digest(external02 / 'collector.snapshot.py'),
            'collector snapshot hashes differ')
    require(f1['request_sha256'] == f2['request_sha256'] == p['request_sha256'],
            'request changed across attempts')
    bindings = {
        'reference_sha256': ROOT / 'data/correction_gate_v11/reader_references_raw.json',
        'questions_sha256': ROOT / 'data/correction_gate_v11/reader_questions.json',
        'source_review_sha256': ROOT / 'results/science_source_review_v11.json',
        'passage_manifest_sha256': ROOT / 'data/correction_gate_v11/passage_manifest.json',
    }
    for name, path in bindings.items():
        require(digest(path) == p[name], 'pre-output binding changed: ' + name)
    pm = load(bindings['passage_manifest_sha256'])
    pack_path = Path(pm['external_passage_pack_path'])
    require(digest(pack_path) == p['passage_pack_sha256'] == pm['external_passage_pack_sha256'],
            'passage pack binding mismatch')
    require(p['native_outputs_at_freeze'] == 0, 'protocol was not recorded before outputs')
    require(stamp(p['frozen_at_utc']) <= stamp(p['reference_join_frozen_at_utc']) <=
            f1['created_unix_seconds'] <= r1['started_unix_seconds'] <= r1['finished_unix_seconds'] <=
            stamp(a['frozen_at_utc']) <= f2['created_unix_seconds'] <= r2['started_unix_seconds'],
            'recorded protocol/reference/amendment chronology invalid')
    require(r2['completion_calls_started'] == r2['completion_processes'] == 2 and
            r2['preflight_processes'] == 1 and r2['all_inputs_preflight_passed'] is True,
            'attempt02 process/call coverage mismatch')

    # Freshly hash the pinned local weights/archive/runtime. No model is loaded.
    for spec in b['assets']:
        path = assets / spec['relative_path']
        require(path.stat().st_size == spec['bytes'] and digest(path) == spec['sha256'],
                'local backend asset differs: ' + spec['relative_path'])
    for spec in b['runtime_files']:
        path = assets / b['runtime_directory'] / spec['path']
        require(path.stat().st_size == spec['bytes'] and digest(path) == spec['sha256'],
                'local runtime artifact differs: ' + spec['path'])
    items = load(external02 / 'request.json')['items']
    require(len(items) == 2 and [x['item_id'] for x in items] == f2['execution_order'],
            'request item order differs')
    require([{'item_id': x['item_id'], 'messages_sha256': canonical(x['messages'])}
             for x in items] == f2['request_projection'], 'message hash binding differs')
    pack = load(pack_path)
    for item in items:
        require(set(item) == {'item_id', 'messages'} and
                [m['role'] for m in item['messages']] == ['system', 'user'] and
                all(set(m) == {'role', 'content'} for m in item['messages']),
                'request includes unexpected/reference fields')
        message = item['messages'][1]['content']
        positions = {}
        for passage in pack['passages']:
            require(message.count(passage['text']) == 1 and
                    text_hash(passage['text']) == passage['text_sha256'],
                    'exact passage omitted, duplicated or changed')
            positions[passage['passage_id']] = message.index(passage['text'])
        require(sorted(positions, key=positions.get) == p['conditions'][item['item_id']],
                'passage order differs from frozen condition')
    rows = load(external02 / 'rendered_prompts.json')
    require(len(rows) == 2 and [x['item_id'] for x in rows] == f2['execution_order'],
            'rendered prompt coverage differs')
    pre = load(external02 / 'preflight_freeze.json')
    require(pre == load(d2 / 'preflight_freeze.json') and pre['completion_calls_at_freeze'] == 0,
            'preflight copies or stage differ')
    for key, path in (('frozen_inputs_sha256', d2 / 'frozen_inputs.json'),
                      ('rendered_prompts_sha256', external02 / 'rendered_prompts.json'),
                      ('prompt_projection_sha256', d2 / 'rendered_prompts.json'),
                      ('props_sha256', external02 / 'preflight.props.json')):
        require(pre[key] == digest(path), 'preflight binding differs: ' + key)
    pre_props = load(external02 / 'preflight.props.json')
    require(pre['chat_template_sha256'] == text_hash(pre_props['chat_template']),
            'native template hash differs')
    require(r2['started_unix_seconds'] <= pre['created_unix_seconds'] <=
            r2['preflight_finished_unix_seconds'], 'preflight timestamps invalid')
    expected_command = [str(assets / b['runtime_directory'] / 'llama-server'), '-m',
        str(assets / b['model_file']), '--host', '127.0.0.1', '--port', str(c['port']),
        '-c', str(b['maximum_context_tokens']), '-t', str(b['threads']), '-tb', str(b['threads']),
        '-b', str(b['batch_tokens']), '-ub', str(b['microbatch_tokens']), '-ngl', '0',
        '--parallel', '1', '--no-warmup', '--no-webui', '-ctk', b['kv_cache_key_type'],
        '-ctv', b['kv_cache_value_type'], '-fa', b['flash_attention'], '--no-context-shift',
        '--jinja', '--chat-template-kwargs', '{"enable_thinking":false}']
    traces = []
    for prefix in ('preflight', 'process_00', 'process_01'):
        trace = load(external02 / (prefix + '.execution.json'))
        require(trace == load(d2 / (prefix + '.execution.json')), 'execution trace copies differ')
        require(trace['command'] == expected_command and trace['process_exit_code'] == 0 and
                not trace.get('watchdog_stop_reason'), 'process arguments/exit/watchdog differ')
        require(trace['model_key'] == 'large' and trace['model_sha256'] == b['model_sha256'] and
                trace['model_repo_revision'] == b['model_repo_revision'] and
                trace['config_sha256'] == digest(bp) and trace['assets'] == b['assets'] and
                trace['runtime_files_sha256'] == canonical(b['runtime_files']), 'backend identity differs')
        require(trace['address_space_limit_bytes'] == b['address_space_limit_bytes'] == 5905580032 and
                trace['cpu_seconds_limit'] == b['cpu_seconds_limit'] == 3600 and
                trace['elapsed_seconds'] <= b['wall_timeout_seconds_per_process'] == 1050 and
                trace['cgroup_resident_observed_peak_bytes'] <= b['cgroup_stop_threshold_bytes'] == 7516192768,
                'resource guard configuration or observation differs')
        props = load(external02 / (prefix + '.props.json'))
        require(props['model_path'] == str(assets / b['model_file']) and props['total_slots'] == 1 and
                props['model_ftype'] == 'Q8_0' and props['build_info'] == 'b11146-7fe450e19' and
                props['default_generation_settings']['n_ctx'] == 4096 and
                props['chat_template'] == pre_props['chat_template'], 'native properties differ')
        log = (external02 / (prefix + '.server.log')).read_text()
        expected_evals = 0 if prefix == 'preflight' else 1
        require(len(re.findall(r'prompt eval time\s*=', log)) == expected_evals,
                'unexpected logged forward-pass coverage')
        traces.append(trace)
    require(len({t['process_id'] for t in traces}) == 3, 'native process reused')
    output_rows = []
    call_times = []
    for index, (item, row) in enumerate(zip(items, rows)):
        prefix = f'process_{index:02d}'
        prompt = row['prompt']; tokens = prompt['input_token_ids']
        require(all(type(x) is int and x >= 0 for x in tokens) and len(tokens) == prompt['input_tokens'] == 3327,
                'native full prompt token count differs')
        require(canonical(tokens) == prompt['input_token_ids_sha256'] and
                text_hash(prompt['rendered_prompt']) == prompt['rendered_prompt_sha256'], 'prompt hash differs')
        rendered = ''.join('<|im_start|>' + m['role'] + '\n' + m['content'] + '<|im_end|>\n'
                           for m in item['messages']) + '<|im_start|>assistant\n<think>\n\n</think>\n\n'
        require(prompt['rendered_prompt'] == rendered, 'full message rendering differs')
        require(len(tokens) + c['maximum_generated_tokens'] <= c['maximum_context_tokens'] == 4096,
                'full generation reserve does not fit')
        request = load(external02 / (prefix + '.request.json'))
        require(request == c['completion_parameters'] | {'prompt': tokens}, 'native request changed')
        require(load(d2 / (prefix + '.request.json')) == c['completion_parameters'] | {
            'prompt_token_ids_sha256': canonical(tokens), 'prompt_tokens': len(tokens)}, 'request projection differs')
        call = load(external02 / (prefix + '.call_started.json'))
        require(call['item_id'] == item['item_id'] and call['index'] == index and
                call['request_sha256'] == canonical(request) and
                call['preflight_freeze_sha256'] == digest(d2 / 'preflight_freeze.json') and
                r2['preflight_finished_unix_seconds'] <= call['started_unix_seconds'] <= r2['finished_unix_seconds'],
                'call identity, freeze or chronology differs')
        call_times.append(call['started_unix_seconds'])
        response = load(external02 / (prefix + '.response.json'))
        require(response['prompt'] == rendered and response['model'] == str(assets / b['model_file']) and
                response['truncated'] is False and response['tokens_evaluated'] == len(tokens),
                'response prompt/model/truncation mismatch')
        timing = response['timings']; predicted = response['tokens_predicted']
        require(timing['cache_n'] == 0 and timing['prompt_n'] == len(tokens) and
                timing['predicted_n'] == predicted and 0 < predicted <= 384,
                'cold native token accounting mismatch')
        require(response['stop'] is True and response['stop_type'] in ('eos', 'word'),
                'unknown stop or exhausted output budget')
        require(isinstance(response['content'], str) and isinstance(response['tokens'], list) and
                all(type(t) is int and t >= 0 for t in response['tokens']), 'invalid returned output records')
        settings = response['generation_settings']
        expected_settings = {k: v for k, v in c['completion_parameters'].items()
                             if k not in ('cache_prompt', 'return_tokens', 'temperature')}
        expected_settings.update(temperature=0.0, generation_prompt='', lora=[], backend_sampling=False)
        require(all(settings.get(k) == v for k, v in expected_settings.items()), 'echoed generation settings differ')
        projection = load(d2 / (prefix + '.response_projection.json'))
        try:
            value = json.loads(response['content']); valid_json = True; top_type = type(value).__name__
        except json.JSONDecodeError:
            valid_json = False; top_type = None
        expected_projection = {'item_id': item['item_id'], 'response_sha256': canonical(response),
            'content_sha256': text_hash(response['content']), 'content_utf8_bytes': len(response['content'].encode()),
            'generated_token_ids_sha256': canonical(response['tokens']),
            'returned_generated_token_count': len(response['tokens']),
            'tokens_evaluated': len(tokens), 'tokens_predicted': predicted, 'truncated': False,
            'stop': True, 'stop_type': response['stop_type'],
            'stopping_word_sha256': text_hash(response.get('stopping_word', '')), 'output_budget_reached': False,
            'timings': timing, 'generation_settings_sha256': canonical(settings),
            'content_export_status': 'withheld_pending_source_copy_review',
            'valid_json': valid_json, 'json_top_level_type': top_type}
        require(projection == expected_projection, 'response projection differs from raw record')
        output_rows.append({'item_id': item['item_id'], 'prompt_tokens': len(tokens),
            'generated_tokens_reported': predicted, 'generated_token_ids_returned': len(response['tokens']),
            'cache_tokens': timing['cache_n'], 'truncated': False, 'stop_type': response['stop_type'],
            'prompt_sha256': prompt['rendered_prompt_sha256'], 'response_file_sha256': digest(external02 / (prefix + '.response.json')),
            'content_sha256': projection['content_sha256'], 'prompt_ms': timing['prompt_ms'],
            'generation_ms': timing['predicted_ms'], 'process_id': traces[index + 1]['process_id']})
    require(call_times[0] < call_times[1], 'completion call order differs')
    require(load(d2 / 'rendered_prompts.json') == [{'item_id': row['item_id'],
        'prompt': {k: v for k, v in row['prompt'].items() if k not in ('rendered_prompt', 'input_token_ids')}}
        for row in rows], 'prompt projection differs')
    bound_paths = [r1p, r2p, cp, bp, ap, pp, s1p, s2p, Path(__file__), *bindings.values()]
    return {'schema_version': 'reader_execution_independent_audit_v0.11', 'status': 'passed',
        'scope': 'Offline native execution provenance only; no medical/scientific interpretation or QA correctness scoring.',
        'input_sha256': {str(path.relative_to(ROOT)): digest(path) for path in bound_paths},
        'external_passage_pack_sha256': digest(pack_path), 'artifact_manifests_checked': manifests,
        'attempt01': {'status': 'retained_prelaunch_failure', 'model_processes_launched': 0,
                      'completion_calls': 0, 'prompt_tokenizations': 0},
        'attempt02': {'status': 'completed', 'preflight_processes': 1, 'completion_processes': 2,
                      'completion_calls': 2, 'distinct_native_processes': 3, 'raw_responses': 2,
                      'rows': output_rows},
        'checks': {'collector_changes_only_data_and_receipt_paths': True,
                   'recorded_pre_output_freeze_and_amendment_chronology': True,
                   'request_reference_passage_bindings': True, 'full_prompt_plus_reserve_fits': True,
                   'exact_native_request_and_response_projections': True,
                   'pinned_assets_and_runtime_rehashed': True, 'resource_guards_unchanged': True,
                   'fresh_processes_no_cached_prompt_no_truncation': True},
        'asset_files_checked': len(b['assets']), 'runtime_files_checked': len(b['runtime_files']),
        'forward_passes_in_audit': 0, 'network_calls_in_audit': 0,
        'source_or_native_content_exported_by_audit': False,
        'limits': ['Recorded timestamps establish internal chronological consistency, not independent historical attestation.',
                   'Token IDs are checked against saved native tokenizer outputs and echoed full prompt; the audit does not rerun a tokenizer.',
                   'Source-copy export approval and strict answer evaluation are separate reviews.',
                   'Observed RSS fields are cumulative child-process high-water marks; no isolated per-run peak claim.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external01', type=Path, default=ROOT.parent / 'tmp/correction_gate_v11/native_reader_attempt01')
    parser.add_argument('--external02', type=Path, default=ROOT.parent / 'tmp/correction_gate_v11/native_reader_attempt02')
    parser.add_argument('--assets', type=Path, default=ROOT.parent / 'tmp/local_backend_v09')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/reader_execution_audit_v11.json')
    args = parser.parse_args()
    result = audit(args.external01.resolve(), args.external02.resolve(), args.assets.resolve())
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'artifact_manifests_checked': result['artifact_manifests_checked'],
                      'native_rows': result['attempt02']['rows'], 'forward_passes_in_audit': 0}))


if __name__ == '__main__':
    main()

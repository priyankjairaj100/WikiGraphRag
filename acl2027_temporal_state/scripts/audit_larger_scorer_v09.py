#!/usr/bin/env python3
"""Independent cached-record audit. No model, tokenizer, network or weight reads."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/larger_scorer_v09'
CONFIG_SHA = '029554e357a3ad553e419db3e38d23c359260b873d1f13332f29e3d665323802'
COLLECTOR_SHA = '67b389f5fe8a87493e245f858607252cb72030f128215a3871db4882303e0dea'
RUNTIME_SHA = '677ee4aa7ac9a70d0d664474f589ba2f35ee2716ea0244d076ab103c3e5304c7'
AMENDMENT_SHA = 'bd5d15299ceed964bd39db0a34feb05bbc89ef61a25f74a2096767cdda218268'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def manifest_check(records):
    for name, expected in records.items():
        path = (ROOT / name).resolve()
        path.relative_to(ROOT)
        require(sha(path) == expected, f'hash mismatch: {name}')
    return len(records)


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def projection_check(value, parent_key=None):
    """Check declared projections do not accidentally contain original inputs."""
    forbidden = {'input_token_ids', 'rendered_prompt', 'source_text', 'messages',
                 'expected_label', 'rationale'}
    if isinstance(value, dict):
        require(not (forbidden & set(value)), 'source-bearing field in projection')
        for key, child in value.items():
            require(key != 'prompt', 'unprojected prompt in cached raw record')
            projection_check(child, key)
    elif isinstance(value, list):
        if parent_key == 'bytes':
            require(len(value) <= 512 and all(type(x) is int and 0 <= x <= 255 for x in value),
                    'invalid single-vocabulary-token byte representation')
            return
        require(not (len(value) > 8 and all(type(x) is int for x in value)),
                'long integer array in projection')
        for child in value:
            projection_check(child)


def audit():
    receipt_path = ROOT / 'results/larger_scorer_v09_receipt.json'
    receipt = read(receipt_path)
    require(receipt['status'] == 'completed', 'collection is not complete; no audit report written')
    for key, expected in [('completed_distributions', 16), ('raw_completed_distributions', 16),
                          ('research_processes', 16), ('preflight_processes', 2)]:
        require(receipt[key] == expected, f'incomplete {key}')
    c = read(ROOT / 'configs/model_backend_v09.json')
    require(sha(ROOT / 'configs/model_backend_v09.json') == CONFIG_SHA, 'unreviewed config')
    require(sha(ROOT / 'scripts/larger_scorer_v09.py') == COLLECTOR_SHA, 'unreviewed collector')
    require(sha(DATA / 'numerical_validation_amendment.json') == AMENDMENT_SHA,
            'unreviewed numerical amendment')
    require(c['probability_mass_absolute_tolerance'] == 1e-4, 'mass tolerance changed')
    directory = DATA / 'research_attempt02'
    frozen = read(directory / 'frozen_inputs.json')
    require(sha(directory / 'frozen_inputs.json') == receipt['frozen_inputs_sha256'],
            'frozen manifest mismatch')
    frozen_count = manifest_check(frozen['source_hashes'])
    current_count = manifest_check(receipt['artifact_hashes'])
    require(set(receipt['artifact_hashes']) == {str(p.relative_to(ROOT)) for p in directory.iterdir()},
            'research artifact coverage mismatch')
    require(sha(directory / 'executed_collector.py.txt') == COLLECTOR_SHA,
            'executed source mismatch')
    projection = frozen['request_projection']
    expected_items = [f'v09_item_{i:02d}' for i in range(1, 5)]
    require([(x['item_id'], x['condition_id']) for x in projection] ==
            [(i, condition) for i in expected_items for condition in ['yes_no', 'no_yes']],
            'item-condition coverage changed')
    histories = {'leadership_disney_2020_2022', 'leadership_intel_2021_2024'}
    require({x['history_id'] for x in projection} == histories, 'history coverage changed')
    expected = [{k: item[k] for k in ['item_id', 'condition_id', 'history_id']} |
                {'model_key': model} for model in ['small', 'large'] for item in projection]
    require(frozen['execution_order'] == expected, 'model execution order changed')
    prompts = json_lines(directory / 'rendered_prompts.jsonl')
    raw = json_lines(directory / 'raw_responses.jsonl')
    require(len(prompts) == len(raw) == 16, 'record count changed')
    require(canonical(c['runtime_files']) == RUNTIME_SHA, 'runtime manifest identity changed')
    neutral_request = {'n_predict': 1, 'temperature': -1.0, 'seed': 7, 'n_probs': 256,
                       'post_sampling_probs': False, 'cache_prompt': False, 'return_tokens': True,
                       'stream': False, 'repeat_penalty': 1.0, 'presence_penalty': 0.0,
                       'frequency_penalty': 0.0, 'logit_bias': [], 'samplers': ['temperature']}
    rows, pids, elapsed, resident_peaks, templates = [], [], [], [], {}
    for model in ['small', 'large']:
        pre = read(directory / f'preflight_{model}.execution.json')
        props = read(directory / f'preflight_{model}.props.json')
        require(pre['process_exit_code'] == 0 and not pre.get('watchdog_stop_reason'),
                'preflight process failed')
        templates[model] = props['chat_template']
    for i, (identity, rendered, record) in enumerate(zip(expected, prompts, raw)):
        projection_check(rendered['prompt'])
        projection_check(record['request'])
        projection_check(record['response'])
        require(all(rendered[k] == v and record[k] == v for k, v in identity.items()),
                'record identity mismatch')
        require(record['process_index'] == i, 'record process index mismatch')
        p, request, response = rendered['prompt'], record['request'], record['response']
        require(set(p) == {'input_token_ids_sha256', 'input_tokens', 'label_tokens',
                           'rendered_prompt_sha256'}, 'unexpected prompt projection shape')
        n = p['input_tokens']
        require(type(n) is int and 0 < n <= c['maximum_research_prompt_tokens'] and
                n + 1 <= c['maximum_context_tokens'], 'token budget violated')
        require(request == neutral_request | {'prompt_token_ids_sha256': p['input_token_ids_sha256'],
                                             'prompt_tokens': n}, 'request policy/hash mismatch')
        require(response['prompt_sha256'] == p['rendered_prompt_sha256'], 'response input hash mismatch')
        require(response['truncated'] is False and response['tokens_evaluated'] == n and
                response['tokens_predicted'] == 1, 'partial or excessive model evaluation')
        timing = response['timings']
        require(timing['cache_n'] == 0 and timing['prompt_n'] == n and timing['predicted_n'] == 1,
                'cold native token accounting failed')
        gs = response['generation_settings']
        for key, value in {'samplers': ['temperature'], 'post_sampling_probs': False,
                           'backend_sampling': False, 'grammar': '', 'logit_bias': [],
                           'repeat_penalty': 1.0, 'presence_penalty': 0.0, 'frequency_penalty': 0.0,
                           'generation_prompt': '', 'lora': [], 'n_probs': 256, 'n_predict': 1}.items():
            require(gs[key] == value, f'unexpected echoed generation setting: {key}')
        require(gs['temperature'] == 0.0, 'unexpected runtime normalized temperature')
        distributions = response['completion_probabilities']
        require(len(distributions) == 1, 'multiple probability positions')
        top = distributions[0]['top_logprobs']
        require(len(top) == 256 and len({x['id'] for x in top}) == 256, 'top-256 coverage mismatch')
        prob = {x['id']: x['logprob'] for x in top}
        require(all(type(x) in (int, float) and math.isfinite(x) and x <= 0 for x in prob.values()),
                'invalid native log probability')
        require(p['label_tokens'] == {'Yes': [9454], 'No': [2753]}, 'native label identity changed')
        y, no = prob[9454], prob[2753]
        odds = y - no
        mass, label_mass = sum(math.exp(x) for x in prob.values()), math.exp(y) + math.exp(no)
        require(mass <= 1.0001 and label_mass <= 1.0001, 'disclosed mass tolerance exceeded')
        execution = read(directory / f'process_{i:02d}.execution.json')
        props = read(directory / f'process_{i:02d}.props.json')
        spec = c if identity['model_key'] == 'large' else c['comparator']
        for key in ['model_id', 'model_sha256', 'model_repo_revision']:
            require(execution[key] == spec[key], 'model binding mismatch')
        require(execution['model_key'] == identity['model_key'] and execution['config_sha256'] == CONFIG_SHA,
                'execution binding mismatch')
        require(execution['runtime_files_sha256'] == RUNTIME_SHA and execution['assets'] == c['assets'],
                'runtime/asset binding mismatch')
        require(execution['process_exit_code'] == 0 and not execution.get('watchdog_stop_reason'),
                'unsuccessful model process')
        require(execution['address_space_limit_bytes'] == 5905580032 and
                execution['cpu_seconds_limit'] == 3600, 'process guard mismatch')
        command = execution['command']
        for flag, value in {'-c': '4096', '-t': '4', '-tb': '4', '-b': '256', '-ub': '128',
                            '-ngl': '0', '--parallel': '1', '-ctk': 'q8_0', '-ctv': 'q8_0',
                            '-fa': 'on', '--host': '127.0.0.1',
                            '--chat-template-kwargs': '{"enable_thinking":false}'}.items():
            require(command[command.index(flag) + 1] == value, f'command flag changed: {flag}')
        require(all(x in command for x in ['--no-context-shift', '--jinja', '--no-warmup']),
                'required runtime flag absent')
        model_path = command[command.index('-m') + 1]
        require(Path(model_path).name == spec['model_file'] and props['model_path'] == model_path and
                response['model'] == model_path, 'native model path mismatch')
        require(props['model_ftype'] == 'Q8_0' and props['total_slots'] == 1 and
                props['build_info'] == 'b11146-7fe450e19', 'native build/type/slots mismatch')
        require(props['chat_template'] == templates[identity['model_key']], 'native template changed')
        require(execution['cgroup_resident_observed_peak_bytes'] <= c['cgroup_stop_threshold_bytes'] and
                execution['elapsed_seconds'] <= c['wall_timeout_seconds_per_process'] + 15,
                'resource observation outside declared bounds')
        pids.append(execution['process_id']);elapsed.append(execution['elapsed_seconds'])
        resident_peaks.append(execution['cgroup_resident_observed_peak_bytes'])
        rows.append(identity | {'input_tokens': n, 'yes_logprob': y, 'no_logprob': no,
                    'logodds': odds, 'literal_label_mass': label_mass, 'returned_top_mass': mass,
                    'decision': 'Yes' if odds > 0 else 'No' if odds < 0 else 'abstain',
                    'prompt_ms': timing['prompt_ms']})
    require(len(set(pids)) == 16, 'research process IDs reused')
    # Matching native encodings are observations here, not assumptions about arbitrary models.
    native_matches = sum(prompts[i]['prompt'] == prompts[i + 8]['prompt'] for i in range(8))
    comparisons = []
    for model in ['small', 'large']:
        for item in expected_items:
            first, second = [r for r in rows if r['model_key'] == model and r['item_id'] == item]
            comparisons.append({'model_key': model, 'item_id': item,
                                'delta_logodds_no_yes_minus_yes_no': second['logodds'] - first['logodds'],
                                'sign_flip': first['logodds'] * second['logodds'] < 0,
                                'decision_changed': first['decision'] != second['decision']})
    derived = read(ROOT / 'results/larger_scorer_v09.json')
    require(len(derived['measurements']) == 16, 'collector-derived result missing')
    for row, original in zip(rows, derived['measurements']):
        require(all(original[k] == value for k, value in row.items()), 'derived arithmetic differs')
    flips = {m: sum(x['sign_flip'] for x in comparisons if x['model_key'] == m)
             for m in ['small', 'large']}
    require(flips == derived['order_sign_flips_by_model'], 'derived flip count differs')
    failed_path = ROOT / 'results/larger_scorer_v09_failed_attempt01_receipt.json'
    failed = read(failed_path)
    require(sha(failed_path) == 'b5444933231d9873370c2c7398df1cba0613f8baa13a7771a4f896a76a449202',
            'failed receipt changed')
    require(failed['status'] == 'failed' and failed['raw_completed_distributions'] == 1 and
            failed['completed_distributions'] == 0, 'discarded-attempt counts differ')
    failed_count = manifest_check(failed['artifact_hashes'])
    discarded = json_lines(DATA / 'research/raw_responses.jsonl')
    require(len(discarded) == 1, 'discarded raw count differs')
    discarded_mass = sum(math.exp(x['logprob']) for x in
                         discarded[0]['response']['completion_probabilities'][0]['top_logprobs'])
    amendment = read(DATA / 'numerical_validation_amendment.json')
    require(discarded_mass == amendment['observed_returned_top256_mass'] and
            discarded_mass > 1 + amendment['previous_absolute_tolerance'] and
            amendment['revised_absolute_tolerance'] == 1e-4,
            'numerical amendment trigger differs from retained raw response')
    smoke = read(DATA / 'smoke_attempt02/receipt.json')
    require(smoke['status'] == 'completed' and smoke['completed_distributions'] == 1,
            'successful smoke accounting differs')
    manifest_check(smoke['artifact_hashes'])
    unsuccessful_smoke = read(DATA / 'smoke_attempt01/receipt.json')
    require(unsuccessful_smoke['status'] == 'failed' and
            unsuccessful_smoke.get('completed_distributions', 0) == 0, 'failed smoke accounting differs')
    optional = ROOT / 'results/larger_scorer_v09_external_audit.json'
    require(optional.exists(), 'saved external audit receipt is not ready; no report written')
    external = {'report_present': optional.exists(), 'independently_reexecuted_by_this_audit': False}
    if optional.exists():
        saved = read(optional)
        require(saved['status'] == 'passed_exact_record_and_projection_comparison' and
                saved['research_rows_checked'] == 16 and saved['source_message_conditions_checked'] == 8 and
                saved['tokenization_checks'] == 48 and saved['new_forward_pass_completions'] == 0 and
                saved['new_model_calls'] == 0, 'external audit receipt coverage/status mismatch')
        require(saved['whole_source_inputs_unchanged'] and saved['condition_only_label_definition_order_changes'] and
                saved['independent_tokenizer_regeneration'] and not saved['independent_score_regeneration'],
                'external audit scope differs')
        require(saved['collection_receipt_sha256'] == sha(receipt_path) and
                saved['request_sha256'] == frozen['external_request_sha256'] and
                saved['external_artifacts'] == receipt['external_artifacts'],
                'external audit does not bind this collection')
        require(saved['matched_models_same_rendered_prompt_bytes'] and
                saved['matched_models_same_input_token_ids'] and native_matches == 8,
                'native matched-input claim differs')
        token_rows = saved['tokenization_records']
        require(len(token_rows) == 48, 'tokenizer receipt count differs')
        for index, row in enumerate(token_rows):
            process_index, suffix_index = divmod(index, 3)
            suffix = ['', 'Yes', 'No'][suffix_index]
            require(row['process_index'] == process_index and row['continuation'] == suffix and
                    row['tokens'] == prompts[process_index]['prompt']['input_tokens'] + bool(suffix),
                    'tokenizer receipt input boundary differs')
            require(all(row[k] == v for k, v in expected[process_index].items() if k != 'history_id'),
                    'tokenizer receipt identity differs')
            trace = row['execution']
            require(trace['returncode'] == 0 and trace['address_space_limit_bytes'] == 1073741824 and
                    trace['cpu_seconds_limit'] == 10 and trace['timeout_seconds'] == 20,
                    'tokenizer process bounds differ')
            if not suffix:
                require(row['canonical_token_ids_sha256'] ==
                        prompts[process_index]['prompt']['input_token_ids_sha256'],
                        'tokenizer prefix hash differs from scorer input')
        source = DATA / 'tokenizer_vocab_only_pinned.cpp.txt'
        require(sha(source) == 'db0cd035294b91009250029a1435a1a688d72eb489c406b630d2293b30d6fb34',
                'tokenizer source evidence changed')
        external.update(report_sha256=sha(optional), report=saved)
    result = {'schema_version': 'independent_larger_scorer_execution_audit_v0.9',
              'status': 'passed', 'audit_script_sha256': sha(Path(__file__)),
              'completed_receipt_sha256': sha(receipt_path),
              'collector_sha256': COLLECTOR_SHA, 'config_sha256': CONFIG_SHA,
              'new_inference_completions': 0, 'new_tokenizer_calls': 0,
              'new_model_weight_hash_reads': 0, 'expected_labels_read': False,
              'artifact_checks': {'current_artifacts': current_count, 'frozen_inputs': frozen_count,
                                  'discarded_attempt_artifacts': failed_count},
              'coverage': {'research_distributions': 16, 'fresh_research_process_ids': 16,
                           'unique_statements': 4, 'source_histories': 2, 'conditions_per_model': 2,
                           'preflight_processes_in_final_attempt': 2,
                           'matching_native_prompt_token_hash_records_across_models': native_matches},
              'accounting': {'accepted_research_distributions': 16,
                             'discarded_research_distributions': 1,
                             'successful_smoke_distributions': 1,
                             'failed_smoke_distributions': 0, 'total_scorer_distributions': 18},
              'probability_mass_absolute_tolerance': 1e-4,
              'discarded_attempt_returned_mass': discarded_mass,
              'returned_mass_range': [min(r['returned_top_mass'] for r in rows),
                                      max(r['returned_top_mass'] for r in rows)],
              'input_token_range': [min(r['input_tokens'] for r in rows), max(r['input_tokens'] for r in rows)],
              'recomputed_measurements': rows, 'recomputed_order_comparisons': comparisons,
              'sign_flips_by_model': flips,
              'resource_observations': {'summed_research_process_elapsed_seconds': sum(elapsed),
                                        'largest_resident_proxy_bytes': max(resident_peaks),
                                        'rss_caveat': 'RUSAGE_CHILDREN maximum is cumulative over prior child processes, not a per-process peak.'},
              'projection_checks': {'full_source_fields_absent': True,
                                    'full_input_token_arrays_absent': True,
                                    'full_source_or_token_reconstruction_verified_here': False},
              'optional_external_audit': external,
              'limitations': ['Cached-record audit cannot itself prove original full-source capture or native tokenization.',
                              'Runtime/assets are bound by collection receipts; this audit does not reread model weights.',
                              'No source-label agreement, natural candidate accuracy or heldout performance is computed.',
                              'Model-size comparisons do not identify causal effects of parameter count.',
                              'Full host libraries/container are not hermetically pinned.']}
    output = ROOT / 'results/larger_scorer_execution_review_v09.json'
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    return result


if __name__ == '__main__':
    result = audit()
    print(json.dumps({k: result[k] for k in ['status', 'coverage', 'accounting', 'sign_flips_by_model']}))

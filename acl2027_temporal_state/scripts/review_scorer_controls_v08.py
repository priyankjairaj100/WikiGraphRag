#!/usr/bin/env python3
"""Independent, offline audit of all v0.8 native process evidence and scores."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re

PROJECT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def audit(project=PROJECT):
    data = project / 'data/scorer_controls_v08'
    config = read(project / 'configs/scorer_controls_v08.json')
    backend = read(project / 'configs/local_backend_v07.json')
    binding = read(project / 'data/likelihood_scoring_v07/backend_binding.json')
    frozen = read(data / 'frozen_inputs.json')
    receipt = read(project / 'results/scorer_controls_v08_receipt.json')
    require(receipt['status'] == 'completed' and receipt['error'] is None and
            receipt['completed_distributions'] == 16, 'incomplete collection')
    require(frozen['completed_distributions_at_freeze'] == 0 and
            frozen['created_unix_seconds'] < receipt['started_unix_seconds'] <
            receipt['finished_unix_seconds'], 'freeze/execution chronology mismatch')
    require(receipt['frozen_inputs_sha256'] == sha(data / 'frozen_inputs.json'), 'freeze binding')
    for path, digest in frozen['source_hashes'].items():
        require(sha(project / path) == digest, f'frozen input hash mismatch: {path}')
    require(sha(data / 'executed_collector.py.txt') ==
            frozen['source_hashes']['scripts/scorer_controls_v08.py'], 'collector snapshot')
    require(config['context_tokens'] == 8192 and config['threads'] == 4 and
            config['address_space_limit_bytes'] == 2147483648 and
            config['cpu_seconds_per_process'] == 2250 and
            config['numerical_comparison_absolute_tolerance'] == 1e-6,
            'control constants changed')
    request = read(project / 'data/likelihood_scoring_v07/request.json')
    prompts = {(r['item_id'], r['condition_id']): r for r in map(json.loads,
               (project / 'data/likelihood_scoring_v07/rendered_prompts.jsonl').read_text().splitlines())}
    first = {}
    for row in request['items']:
        if row['condition_id'] == 'yes_no':
            first.setdefault(row['kind'], row['item_id'])
    require([(r['kind'], r['item_id']) for r in config['selected_items']] == list(first.items()),
            'first item per kind selection changed')
    requested = {(r['item_id'], r['condition_id']): r for r in request['items']}
    order = []
    label_yes = 'Yes: the complete option is adequately supported under the rule for ITEM.kind.'
    label_no = 'No: the complete option is not adequately supported under the rule for ITEM.kind.'
    before = 'LABELS\n' + label_yes + '\n' + label_no
    after = 'LABELS\n' + label_no + '\n' + label_yes
    for selected in config['selected_items']:
        ident = selected['item_id']
        a, b = (prompts[(ident, c)] for c in ('yes_no', 'no_yes'))
        require(a['rendered_prompt'].count(before) == 1 and
                a['rendered_prompt'].replace(before, after) == b['rendered_prompt'],
                f'prompt difference is not only the label display order: {ident}')
        for condition in selected['condition_order']:
            p = prompts[(ident, condition)]
            require(hashlib.sha256(p['rendered_prompt'].encode()).hexdigest() == p['rendered_prompt_sha256'] and
                    canonical(p['input_token_ids']) == p['input_token_ids_sha256'] and
                    len(p['input_token_ids']) == p['input_tokens'] and p['input_tokens'] + 1 <= 8192,
                    f'prompt/token binding: {ident}/{condition}')
            require(canonical(requested[(ident, condition)]['messages']) == p['messages_sha256'],
                    'messages do not match rendered-prompt binding')
            require(p['label_appended_token_ids'] == {label: p['input_token_ids'] + [token] for label, token in (('Yes', 9454), ('No', 2753))},
                    'single label-token boundary binding')
            for cache in ('cold', 'warm'):
                order.append({'item_id': ident, 'kind': selected['kind'], 'condition_id': condition,
                              'cache_condition': cache, 'process_index': len(order) // 2})
    require(order == frozen['execution_order'] and len(order) == 16, 'execution order')
    paths = {f'data/scorer_controls_v08/process_{i:02d}.{suffix}'
             for i in range(8) for suffix in ('execution.json', 'props.json', 'server.log')}
    require(set(receipt['process_artifact_hashes']) == paths, '24 process artifact coverage')
    for path in paths:
        require(sha(project / path) == receipt['process_artifact_hashes'][path], f'artifact hash: {path}')
    require(len(receipt['processes']) == 8, 'process count')
    native_checks = []
    for i in range(8):
        e = read(data / f'process_{i:02d}.execution.json')
        p = read(data / f'process_{i:02d}.props.json')
        log = (data / f'process_{i:02d}.server.log').read_text()
        require(e == receipt['processes'][i], f'execution/receipt mismatch: {i}')
        require(e['assets'] == [{**a, 'verified_sha256': a['sha256']} for a in backend['assets']],
                f'exact pinned asset manifest: {i}')
        require(e['runtime_files'] == backend['runtime_files'] and
                e['runtime_files_sha256'] == canonical(e['runtime_files']) == binding['runtime_files_sha256'] and
                e['runtime_executable_sha256'] == binding['runtime_executable_sha256'], f'runtime binding: {i}')
        cmd = e['command']
        require(Path(cmd[0]).name == 'llama-server' and
                Path(cmd[0]).parent.name == 'llama-b11146', f'executable path: {i}')
        model = str(Path(cmd[0]).parents[2] / backend['model_file'])
        expected_tail = ['-m', model, '--host', '127.0.0.1', '--port', '18088', '-c', '8192',
                         '-t', '4', '-tb', '4', '-b', '256', '-ub', '128', '-ngl', '0',
                         '--parallel', '1', '--no-warmup', '--no-webui', '-ctk', 'q8_0',
                         '-ctv', 'q8_0', '-fa', 'on', '--no-context-shift', '--jinja',
                         '--chat-template-kwargs', '{"enable_thinking":false}']
        require(cmd[1:] == expected_tail, f'exact native command: {i}')
        require(e['context_tokens'] == 8192 and e['address_space_limit_bytes'] == 2147483648 and
                e['cpu_seconds_limit'] == 2250 and e['port'] == 18088, f'process limits: {i}')
        require(e['process_exit_code'] == 0 and type(e['process_id']) is int and e['process_id'] > 0,
                f'process identity/clean exit: {i}')
        require(p['default_generation_settings']['n_ctx'] == 8192 and p['total_slots'] == 1 and
                p['model_path'] == model and p['model_alias'] == model and p['model_ftype'] == 'Q8_0' and
                p['build_info'] == 'b11146-7fe450e19' and p['ui'] is False and
                hashlib.sha256(p['chat_template'].encode()).hexdigest() == binding['chat_template_sha256'],
                f'native props: {i}')
        require('n_threads = 4' in log and 'n_slots = 1, n_ctx_slot = 8192' in log and
                f"loading model '{model}'" in log and 'cleaning up before exit' in log,
                f'native log settings or cleanup: {i}')
        launched = re.findall(r'task (\d+) \| processing task, is_child = 0', log)
        require(len(launched) == 2 and launched[0] == '0' and len(set(launched)) == 2,
                f'exactly two completions in fresh process: {i}')
        require(len(re.findall(r'stop processing: n_tokens = \d+, truncated = 0', log)) == 2,
                f'native completion/truncation log: {i}')
        native_checks.append({'process_index': i, 'pid': e['process_id'], 'exit_code': e['process_exit_code'],
                              'native_command_props_log_and_binding_passed': True,
                              'runtime_file_count': len(e['runtime_files'])})
    require(len({x['pid'] for x in native_checks}) == 8, 'distinct process IDs')
    raw_path = data / 'raw_responses.jsonl'
    require(sha(raw_path) == receipt['raw_responses_sha256'], 'raw response binding')
    records = list(map(json.loads, raw_path.read_text().splitlines()))
    require(len(records) == 16, 'response coverage')
    scores = []
    for row, record in zip(order, records):
        require(all(record[k] == v for k, v in row.items()), 'raw response order')
        tokens = prompts[(row['item_id'], row['condition_id'])]['input_token_ids']
        expected = {'prompt': tokens, 'n_predict': 1, 'temperature': -1.0, 'seed': 7,
                    'n_probs': 256, 'post_sampling_probs': False, 'cache_prompt': row['cache_condition'] == 'warm',
                    'return_tokens': True, 'stream': False, 'repeat_penalty': 1.0, 'presence_penalty': 0.0,
                    'frequency_penalty': 0.0, 'logit_bias': [], 'samplers': ['temperature']}
        require(record['request'] == expected, 'full exact completion payload')
        response = record['response']
        g = response['generation_settings']
        for key, value in {'samplers': ['temperature'], 'n_predict': 1, 'n_probs': 256,
                           'post_sampling_probs': False, 'logit_bias': [], 'grammar': '', 'lora': [],
                           'repeat_penalty': 1.0, 'presence_penalty': 0.0, 'frequency_penalty': 0.0,
                           'backend_sampling': False, 'seed': 7}.items():
            require(g[key] == value, f'native generation policy: {key}')
        # llama.cpp reports its normalized greedy setting as zero for the negative request.
        require(g['temperature'] == 0.0, 'native temperature report')
        n = len(tokens)
        timing = response['timings']
        require(response['model'] == receipt['processes'][row['process_index']]['command'][2] and
                response['id_slot'] == 0 and response['tokens_predicted'] == 1 and
                response['tokens_evaluated'] == n and response['truncated'] is False,
                'native model/slot/token counts')
        cache, prompt = timing['cache_n'], timing['prompt_n']
        require(type(cache) is int and type(prompt) is int and cache >= 0 and prompt > 0 and cache + prompt == n,
                'complete input accounting')
        require((cache == 0 and prompt == n) if row['cache_condition'] == 'cold' else cache > 0,
                'cold/warm cache accounting')
        distributions = response['completion_probabilities']
        require(len(distributions) == 1, 'one next-token distribution')
        top = distributions[0]['top_logprobs']
        require(len(top) == 256 and len({x['id'] for x in top}) == 256, 'top256 coverage and unique IDs')
        lp = {x['id']: x for x in top}
        for ident, label in ((9454, 'Yes'), (2753, 'No')):
            require(ident in lp and lp[ident]['token'] == label and lp[ident]['bytes'] == list(label.encode()) and
                    math.isfinite(lp[ident]['logprob']) and lp[ident]['logprob'] <= 0, 'native literal label')
        yes, no = lp[9454]['logprob'], lp[2753]['logprob']
        require(0 < math.exp(yes) + math.exp(no) <= 1 + 1e-6, 'literal label probability mass')
        scores.append(row | {'input_tokens': n, 'cache_tokens': cache, 'prompt_tokens': prompt,
                             'yes_logprob': yes, 'no_logprob': no, 'logodds': yes - no,
                             'literal_label_mass': math.exp(yes) + math.exp(no)})
    lookup = {(x['item_id'], x['condition_id'], x['cache_condition']): x for x in scores}
    comparisons = []
    for selected in config['selected_items']:
        ident = selected['item_id']
        cells = {f'{c}_{k}': lookup[(ident, c, k)]['logodds']
                 for c in ('yes_no', 'no_yes') for k in ('cold', 'warm')}
        cold_delta = cells['no_yes_cold'] - cells['yes_no_cold']
        warm_delta = cells['no_yes_warm'] - cells['yes_no_warm']
        within = {}
        for c in ('yes_no', 'no_yes'):
            cold, warm = lookup[ident, c, 'cold'], lookup[ident, c, 'warm']
            within[c] = {k: warm[k] - cold[k] for k in ('yes_logprob', 'no_logprob', 'logodds')}
            within[c]['both_labels_within_tolerance'] = all(abs(within[c][k]) <= 1e-6 for k in ('yes_logprob', 'no_logprob'))
        comparisons.append({'item_id': ident, 'kind': selected['kind'], 'logodds_cells': cells,
                            'cold_no_yes_minus_yes_no_logodds': cold_delta,
                            'warm_no_yes_minus_yes_no_logodds': warm_delta,
                            'interaction': warm_delta - cold_delta, 'warm_minus_cold': within,
                            'cold_label_order_sign_change': cells['yes_no_cold'] * cells['no_yes_cold'] < 0})
    summary = read(project / 'results/scorer_controls_v08.json')
    require(len(summary['measurements']) == 16 and len(summary['comparisons']) == 4, 'summary coverage')
    for actual, published in zip(scores, summary['measurements']):
        require(all(published[k] == v for k, v in actual.items()), 'independent score differs from summary')
    for actual, published in zip(comparisons, summary['comparisons']):
        require(all(published[k] == v for k, v in actual.items() if k != 'logodds_cells'),
                'independent comparison differs from summary')
    return {'schema_version': 'independent_scorer_control_review_v0.8', 'status': 'passed',
            'new_model_calls': 0, 'receipt_sha256': sha(project / 'results/scorer_controls_v08_receipt.json'),
            'raw_responses_sha256': sha(raw_path), 'review_script_sha256': sha(Path(__file__)),
            'native_process_checks': native_checks, 'measurements': scores, 'comparisons': comparisons,
            'cold_order_sign_changes': sum(c['cold_label_order_sign_change'] for c in comparisons),
            'warm_repeat_pairs_within_1e_6_each_label': sum(v['both_labels_within_tolerance'] for c in comparisons for v in c['warm_minus_cold'].values()),
            'largest_absolute_warm_minus_cold_logodds': max(abs(v['logodds']) for c in comparisons for v in c['warm_minus_cold'].values()),
            'smallest_absolute_cold_order_logodds_difference': min(abs(c['cold_no_yes_minus_yes_no_logodds']) for c in comparisons),
            'largest_absolute_cold_order_logodds_difference': max(abs(c['cold_no_yes_minus_yes_no_logodds']) for c in comparisons),
            'scope': 'Post-v0.7 diagnostic: four dependent items from one development history; no accuracy, significance, population, or novelty inference.',
            'evidence_limits': ['Resource hard limits are supported by the frozen pre-exec resource.setrlimit code and execution receipts, not independent archived /proc limit snapshots.',
                                'Native /props, response settings and server logs were examined for all eight processes; /props alone does not expose all command or hard-limit settings.',
                                'Fresh apply-template/tokenize response bodies were compared in the frozen collector but not separately archived; original exact v0.7 prompt/token bindings and actual submitted token arrays are retained.',
                                'Per-process asset verification receipts match every pinned file; offline review does not load model binaries or regenerate model outputs.',
                                'Children peak RSS is a collector-lifetime high-water statistic, not an isolated measurement of each individual process.',
                                'Fresh processes control prompt KV carryover; they do not reset OS file caches or establish hermetic execution.']}


if __name__ == '__main__':
    result = audit()
    target = PROJECT / 'results/scorer_control_review_v08.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('native_process_checks', 'measurements', 'comparisons')}))

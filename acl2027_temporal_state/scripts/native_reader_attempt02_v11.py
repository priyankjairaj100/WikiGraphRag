#!/usr/bin/env python3
"""Bounded, source-only native generation diagnostic. No reference labels.

freeze never loads a model; execute first tokenizes every input, freezes that
manifest, and performs at most two completions. All full records stay external.
Malformed answer JSON and token-limit stops are retained failures, never repaired.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

import larger_scorer_v09 as backend
import local_backend_v07 as base

PROJECT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT / 'configs/native_reader_v11.json'
DATA = PROJECT / 'data/native_reader_v11/attempt02'
RECEIPT = PROJECT / 'results/native_reader_attempt02_v11_receipt.json'


def load(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def config():
    c = load(CONFIG)
    require(c['schema_version'] == 'native_reader_config_v0.11', 'wrong configuration')
    require(base.digest(PROJECT / c['backend_config']) == c['backend_config_sha256'],
            'pinned backend config changed')
    require(c['expected_items'] == c['maximum_completion_calls'] == 2, 'call budget changed')
    require(c['maximum_context_tokens'] == 4096 and c['maximum_generated_tokens'] == 384,
            'context/reserve changed')
    require(c['completion_parameters']['n_predict'] == 384, 'generation limit changed')
    return c


def request_items(path):
    value = load(path)
    require(isinstance(value, dict) and set(value) == {'items'}, 'only items are allowed')
    items = value['items']
    require(isinstance(items, list) and len(items) == 2, 'exactly two items are required')
    ids = []
    for item in items:
        require(isinstance(item, dict) and set(item) == {'item_id', 'messages'},
                'item must contain only item_id/messages; no reference fields')
        ident = item['item_id']
        require(isinstance(ident, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident),
                'invalid item ID')
        ids.append(ident)
        messages = item['messages']
        require(isinstance(messages, list) and len(messages) == 2,
                'exact system and user messages required')
        for expected_role, message in zip(('system', 'user'), messages):
            require(isinstance(message, dict) and set(message) == {'role', 'content'},
                    'unexpected message fields')
            require(message['role'] == expected_role and isinstance(message['content'], str)
                    and bool(message['content'].strip()), 'invalid message role/content')
    require(len(set(ids)) == 2, 'duplicate item IDs')
    return items


def external_path(path):
    path = Path(path).resolve()
    require(not path.is_relative_to(PROJECT), 'full source-bearing artifacts must remain external')
    return path


def dry_run(request, protocol):
    c = config()
    items = request_items(request)
    require(Path(protocol).is_file(), 'protocol file absent')
    return {'schema_version': 'native_reader_dry_run_v0.11', 'status': 'passed',
            'request_sha256': base.digest(request), 'protocol_sha256': base.digest(protocol),
            'item_ids': [x['item_id'] for x in items],
            'completion_calls': 0, 'model_processes': 0,
            'maximum_completion_calls': c['maximum_completion_calls'],
            'limitation': 'Offline shape/hash check only; native context preflight is still required.'}


def frozen_sources(protocol):
    return [CONFIG, Path(__file__), PROJECT / 'scripts/larger_scorer_v09.py',
            PROJECT / 'scripts/local_backend_v07.py', PROJECT / 'scripts/fetch_local_backend_v07.py',
            PROJECT / 'configs/model_backend_v09.json', Path(protocol).resolve()]


def freeze(request, protocol, external):
    dry_run(request, protocol)
    external = external_path(external)
    require(not DATA.exists() and not RECEIPT.exists() and not external.exists(),
            'refusing to overwrite any frozen attempt')
    items = request_items(request)
    DATA.mkdir(parents=True)
    external.mkdir(parents=True)
    (external / 'request.json').write_bytes(Path(request).read_bytes())
    (external / 'protocol.snapshot').write_bytes(Path(protocol).read_bytes())
    (external / 'collector.snapshot.py').write_bytes(Path(__file__).read_bytes())
    (external / 'config.snapshot.json').write_bytes(CONFIG.read_bytes())
    sources = frozen_sources(protocol)
    binding = {'schema_version': 'native_reader_frozen_v0.11',
               'created_unix_seconds': time.time(), 'completion_calls_at_freeze': 0,
               'request_sha256': base.digest(request), 'request_bytes': Path(request).stat().st_size,
               'protocol_sha256': base.digest(protocol),
               'source_hashes': {str(p.relative_to(PROJECT)) if p.is_relative_to(PROJECT)
                                 else str(p): base.digest(p) for p in sources},
               'execution_order': [x['item_id'] for x in items],
               'request_projection': [{'item_id': x['item_id'],
                                       'messages_sha256': base.canonical_hash(x['messages'])}
                                      for x in items],
               'config': config()}
    base.write_json(DATA / 'frozen_inputs.json', binding)
    base.write_json(external / 'frozen_inputs.json', binding)
    return {'status': 'frozen', 'frozen_inputs_sha256': base.digest(DATA / 'frozen_inputs.json'),
            'completion_calls': 0}


def check_frozen(external):
    c = config()
    frozen = load(DATA / 'frozen_inputs.json')
    require(frozen['config'] == c and frozen['completion_calls_at_freeze'] == 0,
            'frozen configuration mismatch')
    for name, sha in frozen['source_hashes'].items():
        path = Path(name) if Path(name).is_absolute() else PROJECT / name
        require(base.digest(path) == sha, f'frozen source changed: {name}')
    require(base.digest(external / 'frozen_inputs.json') == base.digest(DATA / 'frozen_inputs.json'),
            'external/project frozen manifests differ')
    require(base.digest(external / 'request.json') == frozen['request_sha256'], 'request changed')
    require(base.digest(external / 'protocol.snapshot') == frozen['protocol_sha256'], 'protocol changed')
    require(base.digest(external / 'collector.snapshot.py') == base.digest(Path(__file__)),
            'collector snapshot changed')
    require(base.digest(external / 'config.snapshot.json') == base.digest(CONFIG),
            'config snapshot changed')
    items = request_items(external / 'request.json')
    require([x['item_id'] for x in items] == frozen['execution_order'], 'execution order changed')
    require([{'item_id': x['item_id'], 'messages_sha256': base.canonical_hash(x['messages'])}
             for x in items] == frozen['request_projection'], 'messages changed')
    return frozen, items


def get_props(port, assets, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/props', timeout=30) as response:
        props = json.load(response)
    base.write_json(path, props)
    backend.check_props(props, assets, 'large')
    return props


def prompt_projection(prompt):
    return {k: v for k, v in prompt.items() if k not in ('rendered_prompt', 'input_token_ids')}


def validate_prompt(prompt):
    c = config()
    tokens = prompt['input_token_ids']
    require(isinstance(tokens, list) and all(type(t) is int and t >= 0 for t in tokens),
            'invalid native token array')
    require(prompt['input_tokens'] == len(tokens) and len(tokens) > 0, 'input count mismatch')
    require(len(tokens) + c['maximum_generated_tokens'] <= c['maximum_context_tokens'],
            'full input plus generation reserve exceeds context; no truncation allowed')
    require(base.canonical_hash(tokens) == prompt['input_token_ids_sha256'], 'token hash mismatch')
    require(hashlib.sha256(prompt['rendered_prompt'].encode()).hexdigest() ==
            prompt['rendered_prompt_sha256'], 'rendered prompt hash mismatch')
    require(prompt['rendered_prompt'].endswith('<think>\n\n</think>\n\n'),
            'thinking-disabled native suffix changed')


def validate_response(prompt, response, assets):
    c = config()
    n = prompt['input_tokens']
    require(response.get('prompt') == prompt['rendered_prompt'], 'response prompt mismatch')
    require(response.get('model') == str(assets / backend.config()['model_file']),
            'response model path mismatch')
    require(response.get('truncated') is False and response.get('tokens_evaluated') == n,
            'input was truncated or incompletely evaluated')
    count = response.get('tokens_predicted')
    require(type(count) is int and 0 < count <= c['maximum_generated_tokens'],
            'generated token count outside frozen budget')
    timing = response['timings']
    require(timing['cache_n'] == 0 and timing['prompt_n'] == n and timing['predicted_n'] == count,
            'cold native token accounting mismatch')
    require(response.get('stop') is True and response.get('stop_type') in ('eos', 'word', 'limit'),
            'native completion lacks known final stop state')
    require(response.get('stop_type') != 'limit', 'native output budget exhausted; no retry')
    require(isinstance(response.get('content'), str), 'missing native response text')
    require(isinstance(response.get('tokens'), list) and
            all(type(x) is int and x >= 0 for x in response['tokens']), 'missing native generated tokens')
    # Some native stop paths omit the terminal stop token from returned token IDs.
    # Preserve both counts rather than assert equality or manufacture a token.
    settings = response['generation_settings']
    for key, value in {'n_predict': 384, 'n_probs': 0, 'seed': 7, 'samplers': ['temperature'],
                       'repeat_penalty': 1.0, 'presence_penalty': 0.0, 'frequency_penalty': 0.0,
                       'grammar': '', 'logit_bias': [], 'post_sampling_probs': False,
                       'generation_prompt': '', 'lora': [], 'backend_sampling': False,
                       'ignore_eos': False, 'stop': [], 'stream': False}.items():
        require(settings.get(key) == value, f'echoed generation setting changed: {key}')
    require(settings['temperature'] == 0.0, 'native greedy temperature normalization changed')


def response_projection(item_id, response):
    content = response['content']
    try:
        parsed = json.loads(content)
        parse = {'valid_json': True, 'json_top_level_type': type(parsed).__name__}
    except json.JSONDecodeError:
        parse = {'valid_json': False, 'json_top_level_type': None}
    return {'item_id': item_id, 'response_sha256': base.canonical_hash(response),
            'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
            'content_utf8_bytes': len(content.encode()),
            'generated_token_ids_sha256': base.canonical_hash(response['tokens']),
            'returned_generated_token_count': len(response['tokens']),
            'tokens_evaluated': response['tokens_evaluated'],
            'tokens_predicted': response['tokens_predicted'], 'truncated': response['truncated'],
            'stop': response['stop'], 'stop_type': response['stop_type'],
            'stopping_word_sha256': hashlib.sha256(response.get('stopping_word', '').encode()).hexdigest(),
            'output_budget_reached': response['stop_type'] == 'limit',
            'timings': response['timings'], 'generation_settings_sha256':
            base.canonical_hash(response['generation_settings']),
            'content_export_status': 'withheld_pending_source_copy_review', **parse}


def save_receipt(receipt, external):
    receipt['external_artifacts'] = {str(p.relative_to(external)): {'bytes': p.stat().st_size,
                                  'sha256': base.digest(p)} for p in sorted(external.rglob('*'))
                                  if p.is_file()}
    receipt['project_artifacts'] = {str(p.relative_to(PROJECT)): base.digest(p)
                                   for p in sorted(DATA.rglob('*')) if p.is_file()}
    base.write_json(RECEIPT, receipt)


def execute(external, assets):
    external = external_path(external)
    assets = Path(assets).resolve()
    frozen, items = check_frozen(external)
    require(not RECEIPT.exists(), 'attempt already started; no retry/resume permitted')
    c = config()
    receipt = {'schema_version': 'native_reader_receipt_v0.11', 'status': 'started',
               'started_unix_seconds': time.time(), 'completion_calls_started': 0,
               'raw_responses_returned': 0, 'validated_responses': 0,
               'frozen_inputs_sha256': base.digest(DATA / 'frozen_inputs.json'),
               'all_inputs_preflight_passed': False, 'preflight_processes': 0,
               'completion_processes': 0, 'error': None}
    save_receipt(receipt, external)
    prompts = []
    try:
        trace = {}
        try:
            receipt['preflight_processes'] += 1
            with backend.server(assets, external / 'preflight.server.log', port=c['port'],
                                trace=trace, model='large') as execution:
                props = get_props(execution['port'], assets, external / 'preflight.props.json')
                for item in items:
                    prompt = base.render_and_tokenize(execution['port'], item['messages'])
                    validate_prompt(prompt)
                    prompts.append({'item_id': item['item_id'], 'prompt': prompt})
        finally:
            base.write_json(external / 'preflight.execution.json', trace)
            base.write_json(DATA / 'preflight.execution.json', trace)
            base.write_json(external / 'rendered_prompts.json', prompts)
        require(trace.get('process_exit_code') == 0 and not trace.get('watchdog_stop_reason'),
                'preflight process failed')
        projected = [{'item_id': row['item_id'], 'prompt': prompt_projection(row['prompt'])}
                     for row in prompts]
        base.write_json(DATA / 'rendered_prompts.json', projected)
        preflight = {'schema_version': 'native_reader_preflight_v0.11',
                     'created_unix_seconds': time.time(), 'completion_calls_at_freeze': 0,
                     'frozen_inputs_sha256': receipt['frozen_inputs_sha256'],
                     'rendered_prompts_sha256': base.digest(external / 'rendered_prompts.json'),
                     'prompt_projection_sha256': base.digest(DATA / 'rendered_prompts.json'),
                     'props_sha256': base.digest(external / 'preflight.props.json'),
                     'chat_template_sha256': hashlib.sha256(props['chat_template'].encode()).hexdigest()}
        base.write_json(DATA / 'preflight_freeze.json', preflight)
        base.write_json(external / 'preflight_freeze.json', preflight)
        receipt['all_inputs_preflight_passed'] = True
        receipt['preflight_finished_unix_seconds'] = time.time()
        save_receipt(receipt, external)
        for index, (item, row) in enumerate(zip(items, prompts)):
            check_frozen(external)
            require(receipt['completion_calls_started'] < c['maximum_completion_calls'],
                    'completion call budget exhausted')
            prefix = f'process_{index:02d}'
            trace = {}
            try:
                receipt['completion_processes'] += 1
                with backend.server(assets, external / f'{prefix}.server.log', port=c['port'],
                                    trace=trace, model='large') as execution:
                    current_props = get_props(execution['port'], assets,
                                              external / f'{prefix}.props.json')
                    require(current_props['chat_template'] == props['chat_template'],
                            'native chat template changed after preflight')
                    prompt = base.render_and_tokenize(execution['port'], item['messages'])
                    validate_prompt(prompt)
                    require(prompt == row['prompt'], 'fresh exact input differs from preflight')
                    request = dict(c['completion_parameters'], prompt=prompt['input_token_ids'])
                    base.write_json(external / f'{prefix}.request.json', request)
                    base.write_json(DATA / f'{prefix}.request.json', c['completion_parameters'] | {
                        'prompt_token_ids_sha256': prompt['input_token_ids_sha256'],
                        'prompt_tokens': prompt['input_tokens']})
                    call_record = {'item_id': item['item_id'], 'index': index,
                                   'started_unix_seconds': time.time(),
                                   'request_sha256': base.canonical_hash(request),
                                   'preflight_freeze_sha256': base.digest(DATA / 'preflight_freeze.json')}
                    base.write_json(external / f'{prefix}.call_started.json', call_record)
                    receipt['completion_calls_started'] += 1
                    save_receipt(receipt, external)
                    try:
                        response = base.post(execution['port'], 'completion', request,
                                             c['per_request_timeout_seconds'])
                    except urllib.error.HTTPError as error:
                        (external / f'{prefix}.http_error_body.bin').write_bytes(error.read())
                        raise
                    base.write_json(external / f'{prefix}.response.json', response)
                    receipt['raw_responses_returned'] += 1
                    save_receipt(receipt, external)
                    require(not execution.get('watchdog_stop_reason'), 'watchdog stopped native process')
                    base.write_json(DATA / f'{prefix}.response_projection.json',
                                    response_projection(item['item_id'], response))
                    validate_response(prompt, response, assets)
                    receipt['validated_responses'] += 1
            finally:
                base.write_json(external / f'{prefix}.execution.json', trace)
                base.write_json(DATA / f'{prefix}.execution.json', trace)
            require(trace.get('process_exit_code') == 0 and not trace.get('watchdog_stop_reason'),
                    'completion process failed')
            save_receipt(receipt, external)
            print(json.dumps({'validated_response': item['item_id'],
                              'of': 2, 'count': receipt['validated_responses']}), flush=True)
        check_frozen(external)
        receipt['status'] = 'completed'
    except BaseException as error:
        # Error text can contain fragments of an invalid response; keep it external.
        base.write_json(external / 'failure.json', {'type': type(error).__name__, 'message': str(error)})
        receipt.update(status='failed', error_type=type(error).__name__,
                       error='See external failure.json and retained process artifacts.')
        raise
    finally:
        receipt['finished_unix_seconds'] = time.time()
        save_receipt(receipt, external)
    return verify(external, assets)


def verify(external, assets):
    """Offline full-input/response audit; never starts a model or calls HTTP."""
    external = external_path(external)
    frozen, items = check_frozen(external)
    receipt = load(RECEIPT)
    require(receipt['status'] == 'completed', 'attempt incomplete; retain failure without repair')
    require(receipt['completion_calls_started'] == receipt['raw_responses_returned'] ==
            receipt['validated_responses'] == receipt['completion_processes'] == 2,
            'completion coverage mismatch')
    require(receipt['all_inputs_preflight_passed'] and receipt['preflight_processes'] == 1,
            'missing all-input preflight')
    for name, spec in receipt['external_artifacts'].items():
        p = external / name
        require(p.stat().st_size == spec['bytes'] and base.digest(p) == spec['sha256'],
                f'external artifact mismatch: {name}')
    for name, sha in receipt['project_artifacts'].items():
        require(base.digest(PROJECT / name) == sha, f'project artifact mismatch: {name}')
    require(set(receipt['project_artifacts']) == {str(p.relative_to(PROJECT)) for p in
            DATA.rglob('*') if p.is_file()}, 'project manifest coverage mismatch')
    require(set(receipt['external_artifacts']) == {str(p.relative_to(external)) for p in
            external.rglob('*') if p.is_file()}, 'external manifest coverage mismatch')
    rows = load(external / 'rendered_prompts.json')
    require(len(rows) == 2 and [x['item_id'] for x in rows] == frozen['execution_order'],
            'rendered item coverage mismatch')
    preflight = load(DATA / 'preflight_freeze.json')
    require(preflight['completion_calls_at_freeze'] == 0 and
            preflight['frozen_inputs_sha256'] == base.digest(DATA / 'frozen_inputs.json') and
            preflight['rendered_prompts_sha256'] == base.digest(external / 'rendered_prompts.json') and
            preflight['prompt_projection_sha256'] == base.digest(DATA / 'rendered_prompts.json'),
            'pre-completion tokenization freeze mismatch')
    require(preflight == load(external / 'preflight_freeze.json') and
            preflight['props_sha256'] == base.digest(external / 'preflight.props.json'),
            'external preflight binding mismatch')
    preflight_trace = load(external / 'preflight.execution.json')
    require(preflight_trace['process_exit_code'] == 0 and not preflight_trace.get('watchdog_stop_reason'),
            'preflight process failed')
    original_props = load(external / 'preflight.props.json')
    require(preflight['chat_template_sha256'] ==
            hashlib.sha256(original_props['chat_template'].encode()).hexdigest(), 'template hash mismatch')
    process_ids = []
    for index, (item, row) in enumerate(zip(items, rows)):
        prefix = f'process_{index:02d}'
        prompt = row['prompt']
        validate_prompt(prompt)
        request = load(external / f'{prefix}.request.json')
        require(request == config()['completion_parameters'] | {'prompt': prompt['input_token_ids']},
                'completion request changed')
        call = load(external / f'{prefix}.call_started.json')
        require(call['request_sha256'] == base.canonical_hash(request) and
                call['preflight_freeze_sha256'] == base.digest(DATA / 'preflight_freeze.json') and
                call['started_unix_seconds'] >= preflight['created_unix_seconds'],
                'call preceded its frozen exact inputs')
        response = load(external / f'{prefix}.response.json')
        validate_response(prompt, response, Path(assets).resolve())
        require(load(DATA / f'{prefix}.response_projection.json') ==
                response_projection(item['item_id'], response), 'response projection mismatch')
        trace = load(external / f'{prefix}.execution.json')
        require(trace['process_exit_code'] == 0 and not trace.get('watchdog_stop_reason'),
                'failed native process')
        require(trace['model_key'] == 'large' and trace['model_sha256'] == backend.config()['model_sha256']
                and trace['config_sha256'] == config()['backend_config_sha256'], 'backend identity mismatch')
        require(trace['address_space_limit_bytes'] == backend.config()['address_space_limit_bytes'] and
                trace['cpu_seconds_limit'] == backend.config()['cpu_seconds_limit'], 'resource guard mismatch')
        props = load(external / f'{prefix}.props.json')
        backend.check_props(props, Path(assets).resolve(), 'large')
        require(props['chat_template'] == original_props['chat_template'], 'fresh process template changed')
        process_ids.append(trace['process_id'])
    require(len(set(process_ids + [preflight_trace['process_id']])) == 3, 'process reused')
    return {'schema_version': 'native_reader_verification_v0.11', 'status': 'passed',
            'validated_responses': 2, 'forward_passes_in_verification': 0,
            'receipt_sha256': base.digest(RECEIPT)}


def export(external, assets, review_path):
    """Export unchanged generated content only after explicit source-copy review."""
    verify(external, assets)
    output = PROJECT / 'results/native_reader_v11_outputs.json'
    require(not output.exists(), 'refusing to overwrite exported responses')
    review = load(review_path)
    require(set(review) == {'schema_version', 'frozen_inputs_sha256', 'responses'} and
            review['schema_version'] == 'native_reader_export_review_v0.11' and
            review['frozen_inputs_sha256'] == base.digest(DATA / 'frozen_inputs.json'),
            'invalid or unbound source-copy export review')
    items = request_items(Path(external) / 'request.json')
    require(len(review['responses']) == 2, 'both responses must receive review')
    rows = []
    for index, (item, approval) in enumerate(zip(items, review['responses'])):
        require(set(approval) == {'item_id', 'content_sha256', 'allow_content_export',
                                 'source_copy_budget_review'}, 'unexpected export review fields')
        response = load(Path(external) / f'process_{index:02d}.response.json')
        require(approval['item_id'] == item['item_id'] and approval['content_sha256'] ==
                hashlib.sha256(response['content'].encode()).hexdigest(), 'review does not bind content')
        require(approval['allow_content_export'] is True and
                isinstance(approval['source_copy_budget_review'], str) and
                bool(approval['source_copy_budget_review'].strip()), 'source-copy review missing')
        rows.append(response_projection(item['item_id'], response) | {
            'content': response['content'], 'content_export_status': 'reviewed_unchanged_native_text'})
    base.write_json(output, {'schema_version': 'native_reader_outputs_v0.11',
                            'frozen_inputs_sha256': base.digest(DATA / 'frozen_inputs.json'),
                            'export_review_sha256': base.digest(review_path), 'responses': rows})
    (PROJECT / 'data/native_reader_v11/export_review.json').write_bytes(Path(review_path).read_bytes())
    return {'status': 'exported', 'responses': len(rows), 'output_sha256': base.digest(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['dry-run', 'freeze', 'execute', 'verify', 'export'])
    parser.add_argument('--request', type=Path)
    parser.add_argument('--protocol', type=Path)
    parser.add_argument('--external', type=Path)
    parser.add_argument('--assets', type=Path, default=backend.DEFAULT_ASSETS)
    parser.add_argument('--review', type=Path)
    args = parser.parse_args()
    if args.mode in ('dry-run', 'freeze'):
        parser.error('--request and --protocol are required') if not (args.request and args.protocol) else None
    if args.mode != 'dry-run' and not args.external:
        parser.error('--external is required')
    if args.mode == 'dry-run':
        result = dry_run(args.request, args.protocol)
    elif args.mode == 'freeze':
        result = freeze(args.request, args.protocol, args.external)
    elif args.mode == 'execute':
        result = execute(args.external, args.assets)
    elif args.mode == 'verify':
        result = verify(args.external, args.assets)
    else:
        if not args.review:
            parser.error('--review is required for export')
        result = export(args.external, args.assets, args.review)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

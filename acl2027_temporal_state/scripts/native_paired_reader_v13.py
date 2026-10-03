#!/usr/bin/env python3
"""Freeze bounded plain-text reader batches; native outputs remain external."""
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
REPOSITORY = PROJECT.parent
DEFAULT_ASSETS = Path('/workspace/scratch/bdef663e3dfc/tmp/local_backend_v09')
PARAMETERS = {'n_predict': 256, 'temperature': -1.0, 'seed': 7, 'n_probs': 0,
              'post_sampling_probs': False, 'cache_prompt': False, 'return_tokens': True,
              'stream': False, 'repeat_penalty': 1.0, 'presence_penalty': 0.0,
              'frequency_penalty': 0.0, 'logit_bias': [], 'samplers': ['temperature'],
              'grammar': '', 'stop': [], 'ignore_eos': False}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    base.write_json(Path(path), value)


def external_path(path):
    path = Path(path).resolve()
    require(not path.is_relative_to(REPOSITORY), 'Source-bearing artifacts must remain outside repository.')
    return path


def config(path):
    c = load(path)
    expected = {'schema_version', 'batch_id', 'expected_requests', 'maximum_context_tokens',
                'maximum_input_tokens', 'maximum_generated_tokens', 'port',
                'per_request_timeout_seconds', 'system_prompt'}
    require(set(c) == expected and c['schema_version'] == 'native_paired_reader_config_v0.13',
            'Unexpected configuration fields/schema.')
    require(isinstance(c['batch_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', c['batch_id']), 'Invalid batch ID.')
    require(type(c['expected_requests']) is int and c['expected_requests'] in (2, 16), 'Only frozen 2/16-call batches allowed.')
    require(c['maximum_context_tokens'] == 4096 and c['maximum_generated_tokens'] == 256, 'Fixed context/output reserve changed.')
    require(type(c['maximum_input_tokens']) is int and 1 <= c['maximum_input_tokens'] <= 3840, 'Invalid full-input cap.')
    require(type(c['port']) is int and 1024 <= c['port'] <= 65535, 'Invalid local port.')
    require(type(c['per_request_timeout_seconds']) is int and 1 <= c['per_request_timeout_seconds'] <= 900, 'Invalid request timeout.')
    require(isinstance(c['system_prompt'], str) and c['system_prompt'].strip(), 'System prompt required.')
    return c


def request_items(path, expected):
    items = load(path)
    require(isinstance(items, list) and len(items) == expected, 'Request count differs from frozen batch.')
    ids = []
    for item in items:
        require(isinstance(item, dict) and set(item) == {'request_id', 'prompt'}, 'Only request_id/prompt allowed; no labels.')
        require(isinstance(item['request_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', item['request_id']), 'Invalid request ID.')
        require(isinstance(item['prompt'], str) and item['prompt'].strip(), 'Nonempty prompt required.')
        ids.append(item['request_id'])
    require(len(set(ids)) == len(ids), 'Duplicate request IDs.')
    return items


def messages(item, c):
    return [{'role': 'system', 'content': c['system_prompt']}, {'role': 'user', 'content': item['prompt']}]


def freeze(args):
    c = config(args.config)
    items = request_items(args.request, c['expected_requests'])
    external = external_path(args.external)
    data, receipt = args.data_dir.resolve(), args.receipt.resolve()
    require(data.is_relative_to(PROJECT) and receipt.is_relative_to(PROJECT), 'Metadata destinations must lie in project.')
    require(not external.exists() and not data.exists() and not receipt.exists(), 'Refusing overwrite of any batch attempt.')
    protocol, review = args.protocol.resolve(), args.reference_review.resolve()
    require(protocol.is_file() and review.is_file(), 'Protocol and pre-output reference review must exist.')
    assets = args.assets.resolve()
    asset_binding = backend.verify_assets(assets)  # Hashes only; no model or network launch.
    sources = [Path(__file__).resolve(), PROJECT / 'scripts/larger_scorer_v09.py',
               PROJECT / 'scripts/local_backend_v07.py', PROJECT / 'scripts/fetch_local_backend_v07.py',
               PROJECT / 'configs/model_backend_v09.json', args.config.resolve(), protocol, review]
    frozen = {'schema_version': 'native_paired_reader_freeze_v0.13', 'created_unix_seconds': time.time(),
              'completion_calls_at_freeze': 0, 'config': c, 'parameters': PARAMETERS,
              'request_sha256': base.digest(args.request), 'request_bytes': args.request.stat().st_size,
              'protocol_sha256': base.digest(protocol), 'reference_review_sha256': base.digest(review),
              'reference_review_supplied_to_model': False,
              'source_hashes': {str(p): base.digest(p) for p in sources},
              'asset_binding': asset_binding, 'assets_path': str(assets),
              'external_path': str(external), 'data_dir': str(data), 'receipt_path': str(receipt),
              'execution_order': [item['request_id'] for item in items],
              'request_projection': [{'request_id': item['request_id'], 'messages_sha256': base.canonical_hash(messages(item,c))} for item in items]}
    external.mkdir(parents=True); data.mkdir(parents=True)
    for name, path in [('request.json', args.request), ('protocol.snapshot', protocol),
                       ('reference_review.snapshot', review), ('config.snapshot.json', args.config),
                       ('collector.snapshot.py', Path(__file__))]:
        (external / name).write_bytes(Path(path).read_bytes())
    write(data / 'frozen_inputs.json', frozen); write(external / 'frozen_inputs.json', frozen)
    return {'status': 'frozen_no_model', 'frozen_inputs_sha256': base.digest(data / 'frozen_inputs.json'), 'requests': len(items)}


def check_frozen(external):
    external = external_path(external)
    f = load(external / 'frozen_inputs.json'); data = Path(f['data_dir'])
    require(f['external_path'] == str(external) and data.is_relative_to(PROJECT), 'Frozen location mismatch.')
    require(base.digest(external / 'frozen_inputs.json') == base.digest(data / 'frozen_inputs.json'), 'Frozen manifests differ.')
    require(f['completion_calls_at_freeze'] == 0 and f['parameters'] == PARAMETERS, 'Frozen parameter mismatch.')
    for path, sha in f['source_hashes'].items():
        require(base.digest(Path(path)) == sha, 'Frozen source changed: ' + path)
    for filename, expected in [('request.json', f['request_sha256']), ('protocol.snapshot', f['protocol_sha256']),
                               ('reference_review.snapshot', f['reference_review_sha256']),
                               ('collector.snapshot.py', base.digest(Path(__file__)))]:
        require(base.digest(external / filename) == expected, 'Frozen snapshot changed: ' + filename)
    require(config(external / 'config.snapshot.json') == f['config'], 'Frozen config changed.')
    items = request_items(external / 'request.json', f['config']['expected_requests'])
    require([x['request_id'] for x in items] == f['execution_order'], 'Request order changed.')
    require([{'request_id': x['request_id'], 'messages_sha256': base.canonical_hash(messages(x,f['config']))} for x in items] == f['request_projection'], 'Request messages changed.')
    return f, items


def validate_prompt(prompt, c):
    tokens = prompt['input_token_ids']
    require(isinstance(tokens, list) and tokens and all(type(x) is int and x >= 0 for x in tokens), 'Invalid native token array.')
    require(prompt['input_tokens'] == len(tokens), 'Native token count mismatch.')
    require(len(tokens) <= c['maximum_input_tokens'] and len(tokens) + c['maximum_generated_tokens'] <= c['maximum_context_tokens'], 'Full prompt exceeds frozen budget; no truncation allowed.')
    require(base.canonical_hash(tokens) == prompt['input_token_ids_sha256'], 'Token hash mismatch.')
    require(hashlib.sha256(prompt['rendered_prompt'].encode()).hexdigest() == prompt['rendered_prompt_sha256'], 'Prompt hash mismatch.')
    require(prompt['rendered_prompt'].endswith('<think>\n\n</think>\n\n'), 'Thinking-disabled native suffix changed.')


def props(port, assets, destination):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/props', timeout=30) as response:
        value = json.load(response)
    write(destination, value); backend.check_props(value, assets, 'large')
    return value


def validate_response(prompt, response, assets):
    n = prompt['input_tokens']
    require(response.get('prompt') == prompt['rendered_prompt'], 'Returned prompt mismatch.')
    require(response.get('model') == str(assets / backend.config()['model_file']), 'Returned model mismatch.')
    require(response.get('truncated') is False and response.get('tokens_evaluated') == n, 'Input truncated/incompletely evaluated.')
    count = response.get('tokens_predicted')
    require(type(count) is int and 0 < count <= PARAMETERS['n_predict'], 'Output count outside budget.')
    timing = response['timings']
    require(timing['cache_n'] == 0 and timing['prompt_n'] == n and timing['predicted_n'] == count, 'Cold token accounting mismatch.')
    require(response.get('stop') is True and response.get('stop_type') in ('eos','word','limit'), 'Unknown native final stop state.')
    require(isinstance(response.get('content'), str), 'Missing native text.')
    require(isinstance(response.get('tokens'),list) and all(type(x) is int and x >= 0 for x in response['tokens']), 'Missing native output tokens.')
    settings = response['generation_settings']
    expected = {k:v for k,v in PARAMETERS.items() if k not in ('temperature','cache_prompt','return_tokens')}
    expected.update(generation_prompt='', lora=[], backend_sampling=False, temperature=0.0)
    for key, value in expected.items():
        require(settings.get(key) == value, 'Echoed generation setting changed: ' + key)


def project_prompt(prompt):
    return {k:v for k,v in prompt.items() if k not in ('rendered_prompt','input_token_ids')}


def project_response(request_id, response):
    return {'request_id': request_id, 'response_sha256': base.canonical_hash(response),
            'content_sha256': hashlib.sha256(response['content'].encode()).hexdigest(),
            'content_utf8_bytes': len(response['content'].encode()),
            'generated_token_ids_sha256': base.canonical_hash(response['tokens']),
            'returned_generated_token_count': len(response['tokens']),
            'tokens_evaluated': response['tokens_evaluated'], 'tokens_predicted': response['tokens_predicted'],
            'truncated': response['truncated'], 'stop': response['stop'], 'stop_type': response['stop_type'],
            'output_budget_failure': response['stop_type'] == 'limit', 'timings': response['timings'],
            'generation_settings_sha256': base.canonical_hash(response['generation_settings']),
            'content_export_status': 'external_only_no_source_copy_review'}


def save(receipt, external, data, path):
    receipt['external_artifacts'] = {str(p.relative_to(external)): {'bytes': p.stat().st_size, 'sha256': base.digest(p)} for p in sorted(external.rglob('*')) if p.is_file()}
    receipt['project_artifacts'] = {str(p.relative_to(PROJECT)): base.digest(p) for p in sorted(data.rglob('*')) if p.is_file()}
    write(path, receipt)


def execute(external):
    external = external_path(external); f, items = check_frozen(external)
    data, output, assets = Path(f['data_dir']), Path(f['receipt_path']), Path(f['assets_path'])
    c = f['config']
    require(not output.exists(), 'Batch already started; retry/resume forbidden.')
    require(backend.verify_assets(assets) == f['asset_binding'], 'Model/runtime changed after freeze.')
    receipt = {'schema_version': 'native_paired_reader_receipt_v0.13', 'batch_id':c['batch_id'],
               'status':'started','started_unix_seconds':time.time(),'completion_calls_started':0,
               'raw_responses_returned':0,'validated_responses':0,'output_budget_failures':0,
               'preflight_processes':0,'completion_processes':0,'all_inputs_preflight_passed':False,
               'frozen_inputs_sha256':base.digest(data/'frozen_inputs.json')}
    save(receipt,external,data,output)
    rendered = []
    try:
        trace={}
        try:
            receipt['preflight_processes'] += 1
            with backend.server(assets,external/'preflight.server.log',port=c['port'],trace=trace,model='large') as execution:
                original_props=props(execution['port'],assets,external/'preflight.props.json')
                for item in items:
                    prompt=base.render_and_tokenize(execution['port'],messages(item,c))
                    validate_prompt(prompt,c)
                    rendered.append({'request_id':item['request_id'],'prompt':prompt})
        finally:
            write(external/'preflight.execution.json',trace);write(data/'preflight.execution.json',trace)
            write(external/'rendered_prompts.json',rendered)
        require(trace.get('process_exit_code')==0 and not trace.get('watchdog_stop_reason'),'Preflight process failed.')
        write(data/'rendered_prompts.json',[{'request_id':row['request_id'],'prompt':project_prompt(row['prompt'])} for row in rendered])
        preflight={'created_unix_seconds':time.time(),'completion_calls_at_freeze':0,
                   'frozen_inputs_sha256':base.digest(data/'frozen_inputs.json'),
                   'rendered_prompts_sha256':base.digest(external/'rendered_prompts.json'),
                   'projected_prompts_sha256':base.digest(data/'rendered_prompts.json'),
                   'props_sha256':base.digest(external/'preflight.props.json')}
        write(data/'preflight_freeze.json',preflight);write(external/'preflight_freeze.json',preflight)
        receipt['all_inputs_preflight_passed']=True
        save(receipt,external,data,output)
        for index,(item,row) in enumerate(zip(items,rendered)):
            check_frozen(external)
            require(receipt['completion_calls_started'] < c['expected_requests'],'Frozen call budget exhausted.')
            prefix=f'process_{index:02d}';trace={}
            try:
                receipt['completion_processes']+=1
                with backend.server(assets,external/f'{prefix}.server.log',port=c['port'],trace=trace,model='large') as execution:
                    current_props=props(execution['port'],assets,external/f'{prefix}.props.json')
                    require(current_props['chat_template']==original_props['chat_template'],'Template changed.')
                    prompt=base.render_and_tokenize(execution['port'],messages(item,c));validate_prompt(prompt,c)
                    require(prompt==row['prompt'],'Fresh-process prompt differs from preflight.')
                    request=PARAMETERS | {'prompt':prompt['input_token_ids']}
                    write(external/f'{prefix}.request.json',request)
                    write(data/f'{prefix}.request.json',PARAMETERS | {'prompt_tokens':prompt['input_tokens'],'prompt_token_ids_sha256':prompt['input_token_ids_sha256']})
                    write(external/f'{prefix}.call_started.json',{'request_id':item['request_id'],'index':index,
                          'started_unix_seconds':time.time(),'request_sha256':base.canonical_hash(request),
                          'preflight_freeze_sha256':base.digest(data/'preflight_freeze.json')})
                    receipt['completion_calls_started']+=1;save(receipt,external,data,output)
                    try:
                        response=base.post(execution['port'],'completion',request,c['per_request_timeout_seconds'])
                    except urllib.error.HTTPError as error:
                        (external/f'{prefix}.http_error_body.bin').write_bytes(error.read());raise
                    write(external/f'{prefix}.response.json',response)
                    receipt['raw_responses_returned']+=1;save(receipt,external,data,output)
                    require(not execution.get('watchdog_stop_reason'),'Watchdog stopped process.')
                    validate_response(prompt,response,assets)
                    write(data/f'{prefix}.response_projection.json',project_response(item['request_id'],response))
                    receipt['validated_responses']+=1
                    receipt['output_budget_failures']+=int(response['stop_type']=='limit')
            finally:
                write(external/f'{prefix}.execution.json',trace);write(data/f'{prefix}.execution.json',trace)
            require(trace.get('process_exit_code')==0 and not trace.get('watchdog_stop_reason'),'Completion process failed.')
            save(receipt,external,data,output)
            print(json.dumps({'request_id':item['request_id'],'validated':receipt['validated_responses'],'total':c['expected_requests'],'output_budget_failures':receipt['output_budget_failures']}),flush=True)
        check_frozen(external);receipt['status']='completed'
    except BaseException as error:
        write(external/'failure.json',{'type':type(error).__name__,'message':str(error)})
        receipt.update(status='failed',error_type=type(error).__name__,error='See retained external failure.json; no repair or retry.')
        raise
    finally:
        receipt['finished_unix_seconds']=time.time();save(receipt,external,data,output)
    return verify(external)


def verify(external):
    external=external_path(external);f,items=check_frozen(external)
    data,assets=Path(f['data_dir']),Path(f['assets_path']);receipt=load(f['receipt_path'])
    n=f['config']['expected_requests']
    require(receipt['status']=='completed' and all(receipt[k]==n for k in ['completion_calls_started','raw_responses_returned','validated_responses','completion_processes']),'Incomplete batch; preserve failures.')
    require(receipt['all_inputs_preflight_passed'] and receipt['preflight_processes']==1,'Missing preflight.')
    for name,spec in receipt['external_artifacts'].items():
        p=external/name;require(p.stat().st_size==spec['bytes'] and base.digest(p)==spec['sha256'],'External artifact changed: '+name)
    for name,sha in receipt['project_artifacts'].items():
        require(base.digest(PROJECT/name)==sha,'Project artifact changed: '+name)
    require(set(receipt['external_artifacts'])=={str(p.relative_to(external)) for p in external.rglob('*') if p.is_file()},'External artifact inventory changed.')
    require(set(receipt['project_artifacts'])=={str(p.relative_to(PROJECT)) for p in data.rglob('*') if p.is_file()},'Project artifact inventory changed.')
    rows=load(external/'rendered_prompts.json');preflight=load(data/'preflight_freeze.json')
    require([x['request_id'] for x in rows]==f['execution_order'],'Preflight request coverage mismatch.')
    require(preflight==load(external/'preflight_freeze.json') and preflight['completion_calls_at_freeze']==0 and preflight['frozen_inputs_sha256']==base.digest(data/'frozen_inputs.json') and preflight['rendered_prompts_sha256']==base.digest(external/'rendered_prompts.json') and preflight['projected_prompts_sha256']==base.digest(data/'rendered_prompts.json') and preflight['props_sha256']==base.digest(external/'preflight.props.json'),'Pre-completion freeze mismatch.')
    initial=load(external/'preflight.execution.json');process_ids=[initial.get('process_id')]
    require(initial.get('process_exit_code')==0 and not initial.get('watchdog_stop_reason'),'Failed preflight process.')
    original_props=load(external/'preflight.props.json');budget_failures=0
    for i,(item,row) in enumerate(zip(items,rows)):
        prefix=f'process_{i:02d}';prompt=row['prompt'];validate_prompt(prompt,f['config'])
        request=load(external/f'{prefix}.request.json')
        require(request==PARAMETERS | {'prompt':prompt['input_token_ids']},'Completion request changed.')
        call=load(external/f'{prefix}.call_started.json')
        require(call['request_id']==item['request_id'] and call['request_sha256']==base.canonical_hash(request) and call['preflight_freeze_sha256']==base.digest(data/'preflight_freeze.json') and call['started_unix_seconds']>=preflight['created_unix_seconds'],'Call precedes or differs from frozen input.')
        response=load(external/f'{prefix}.response.json');validate_response(prompt,response,assets)
        require(load(data/f'{prefix}.response_projection.json')==project_response(item['request_id'],response),'Response projection changed.')
        trace=load(external/f'{prefix}.execution.json')
        require(trace.get('process_exit_code')==0 and not trace.get('watchdog_stop_reason'),'Failed completion process.')
        require(trace['model_key']=='large' and trace['model_sha256']==backend.config()['model_sha256'] and trace['runtime_files_sha256']==f['asset_binding']['runtime_files_sha256'] and trace['config_sha256']==f['asset_binding']['config_sha256'],'Backend identity changed.')
        require(trace['address_space_limit_bytes']==backend.config()['address_space_limit_bytes'] and trace['cpu_seconds_limit']==backend.config()['cpu_seconds_limit'],'Resource guard changed.')
        current_props=load(external/f'{prefix}.props.json');backend.check_props(current_props,assets,'large')
        require(current_props['chat_template']==original_props['chat_template'],'Template changed.')
        process_ids.append(trace['process_id']);budget_failures+=int(response['stop_type']=='limit')
    require(None not in process_ids and len(set(process_ids))==n+1,'Process reuse/missing process ID.')
    require(receipt['output_budget_failures']==budget_failures,'Output-budget failure accounting changed.')
    return {'status':'passed','validated_responses':n,'output_budget_failures':budget_failures,'model_calls_during_verification':0,'receipt_sha256':base.digest(Path(f['receipt_path']))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['freeze','execute','verify'])
    parser.add_argument('--external',type=Path,required=True)
    for name in ['config','request','protocol','reference-review','data-dir','receipt']:
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--assets',type=Path,default=DEFAULT_ASSETS)
    args=parser.parse_args()
    if args.mode=='freeze':
        if not all(getattr(args,k) for k in ['config','request','protocol','reference_review','data_dir','receipt']):
            parser.error('freeze requires config, request, protocol, reference-review, data-dir and receipt')
        result=freeze(args)
    elif args.mode=='execute':
        result=execute(args.external)
    else:
        result=verify(args.external)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()

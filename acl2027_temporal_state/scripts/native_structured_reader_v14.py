#!/usr/bin/env python3
"""Configurable pinned native reader and shared tokenizer bridge; no downloads."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import larger_scorer_v09 as guard
import fetch_local_backend_v07 as fetch
import native_paired_reader_v13 as previous
from contextlib import contextmanager
import os
import resource
import subprocess
import threading
import local_backend_v07 as base

PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent

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
                'per_request_timeout_seconds', 'system_prompt', 'model', 'runtime'}
    require(set(c) == expected and c['schema_version'] == 'native_structured_reader_config_v0.14',
            'Unexpected configuration fields/schema.')
    require(isinstance(c['batch_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', c['batch_id']), 'Invalid batch ID.')
    require(type(c['expected_requests']) is int and c['expected_requests'] in (8, 32), 'Only frozen 8-control/32-main batches allowed.')
    require(c['maximum_context_tokens'] == 4096 and c['maximum_generated_tokens'] == 256, 'Fixed context/output reserve changed.')
    require(type(c['maximum_input_tokens']) is int and c['maximum_input_tokens'] == 3500, 'Invalid full-input cap.')
    require(type(c['port']) is int and 1024 <= c['port'] <= 65535, 'Invalid local port.')
    require(type(c['per_request_timeout_seconds']) is int and 1 <= c['per_request_timeout_seconds'] <= 900, 'Invalid request timeout.')
    require(isinstance(c['system_prompt'], str) and c['system_prompt'].strip(), 'System prompt required.')
    require(set(c['model']) == {'path','repo','revision','bytes','sha256'}, 'Unexpected model binding fields.')
    require(Path(c['model']['path']).is_absolute() and Path(c['model']['path']).suffix == '.gguf', 'Absolute GGUF model path required.')
    require(type(c['model']['bytes']) is int and 0<c['model']['bytes']<5905580032, 'Model cannot fit hard address-space guard.')
    require(re.fullmatch(r'[0-9a-f]{64}',c['model']['sha256']) and re.fullmatch(r'[0-9a-f]{40}',c['model']['revision']), 'Immutable model hash/revision required.')
    require(set(c['runtime']) == {'directory','base_config','base_config_sha256'}, 'Unexpected runtime fields.')
    require(c['runtime']['base_config_sha256']=='029554e357a3ad553e419db3e38d23c359260b873d1f13332f29e3d665323802', 'Frozen legacy guard/runtime configuration required.')
    require(base.digest(PROJECT/c['runtime']['base_config'])==c['runtime']['base_config_sha256'], 'Runtime/guard config changed.')
    require(Path(c['runtime']['directory']).is_absolute(), 'Absolute runtime directory required.')
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
    assets = Path(c['model']['path']).parent
    backend = ConfiguredBackend(c)
    asset_binding = backend.verify_assets(assets)  # Hashes only; no model or network launch.
    sources = [Path(__file__).resolve(), PROJECT / 'scripts/larger_scorer_v09.py',
               PROJECT / 'scripts/local_backend_v07.py', PROJECT / 'scripts/fetch_local_backend_v07.py',
               PROJECT / 'scripts/native_paired_reader_v13.py',
               PROJECT / 'configs/model_backend_v09.json', args.config.resolve(), protocol, review]
    frozen = {'schema_version': 'native_structured_reader_freeze_v0.14', 'created_unix_seconds': time.time(),
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
    require(isinstance(prompt['rendered_prompt'],str) and prompt['rendered_prompt'], 'Empty native template output.')
    # Native template and server enable_thinking=false are frozen; no old-model suffix is imposed.


def props(port, assets, destination, backend):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/props', timeout=30) as response:
        value = json.load(response)
    write(destination, value); backend.check_props(value, assets, 'large')
    return value


def validate_response(prompt, response, assets, backend):
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
    return previous.project_prompt(prompt)


def project_response(request_id, response):
    return previous.project_response(request_id,response)


def save(receipt, external, data, path):
    receipt['external_artifacts'] = {str(p.relative_to(external)): {'bytes': p.stat().st_size, 'sha256': base.digest(p)} for p in sorted(external.rglob('*')) if p.is_file()}
    receipt['project_artifacts'] = {str(p.relative_to(PROJECT)): base.digest(p) for p in sorted(data.rglob('*')) if p.is_file()}
    write(path, receipt)


def execute(external):
    external = external_path(external); f, items = check_frozen(external)
    data, output, assets = Path(f['data_dir']), Path(f['receipt_path']), Path(f['assets_path'])
    c = f['config']
    backend = ConfiguredBackend(c)
    require(not output.exists(), 'Batch already started; retry/resume forbidden.')
    require(backend.verify_assets(assets) == f['asset_binding'], 'Model/runtime changed after freeze.')
    receipt = {'schema_version': 'native_structured_reader_receipt_v0.14', 'batch_id':c['batch_id'],
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
                original_props=props(execution['port'],assets,external/'preflight.props.json',backend)
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
                    current_props=props(execution['port'],assets,external/f'{prefix}.props.json',backend)
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
                    validate_response(prompt,response,assets,backend)
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
    backend=ConfiguredBackend(f['config'])
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
        response=load(external/f'{prefix}.response.json');validate_response(prompt,response,assets,backend)
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


class ConfiguredBackend:
    """Pinned runtime with a separately pinned selected model; no download API."""
    def __init__(self,c):
        self.reader_config=c

    def config(self):
        c=load(PROJECT/self.reader_config['runtime']['base_config'])
        model=self.reader_config['model']
        return c | {'model_id':model['repo'],'model_file':Path(model['path']).name,
                    'model_sha256':model['sha256'],'model_repo_revision':model['revision'],
                    'runtime_directory':self.reader_config['runtime']['directory']}

    def verify_assets(self,assets):
        c=self.config();model=self.reader_config['model'];path=Path(model['path'])
        require(Path(assets).resolve()==path.parent.resolve(),'Model asset directory mismatch.')
        require(path.is_file() and path.stat().st_size==model['bytes'] and base.digest(path)==model['sha256'],'Selected model bytes/hash mismatch.')
        baseline=PROJECT/self.reader_config['runtime']['base_config']
        require(base.digest(baseline)==self.reader_config['runtime']['base_config_sha256'],'Pinned runtime/guard configuration changed.')
        files=fetch.inspect_runtime(Path(c['runtime_directory']),c)
        return {'config_sha256':base.digest(baseline),'selected_model':model,
                'runtime_files_sha256':base.canonical_hash(files),
                'reader_config_sha256_canonical':base.canonical_hash(self.reader_config)}

    def check_props(self,value,assets,model):
        require(value.get('model_path')==str(Path(self.reader_config['model']['path'])) and value.get('total_slots')==1,'Native model identity or slot count differs.')

    @contextmanager
    def server(self, assets, log_path, *, cpu_seconds=None, port=18089, trace=None, model='large'):
        c=self.config()
        binding=trace if trace is not None else {}
        binding.update(self.verify_assets(assets))
        require(model=='large','Only the frozen selected model is available.')
        require(cpu_seconds is None or cpu_seconds==c['cpu_seconds_limit'],'CPU guard override forbidden.')
        model_spec=c
        binding.update(model_key=model,model_id=model_spec['model_id'],model_sha256=model_spec['model_sha256'],
                       model_repo_revision=model_spec['model_repo_revision'])
        runtime=assets/c['runtime_directory']
        command=[str(runtime/'llama-server'),'-m',str(assets/model_spec['model_file']),
                 '--host','127.0.0.1','--port',str(port),'-c',str(c['maximum_context_tokens']),
                 '-t',str(c['threads']),'-tb',str(c['threads']),'-b',str(c['batch_tokens']),
                 '-ub',str(c['microbatch_tokens']),'-ngl','0','--parallel','1',
                 '--no-warmup','--no-webui','-ctk',c['kv_cache_key_type'],
                 '-ctv',c['kv_cache_value_type'],'-fa',c['flash_attention'],
                 '--no-context-shift','--jinja','--chat-template-kwargs','{"enable_thinking":false}']
        cpu=cpu_seconds or c['cpu_seconds_limit']
        def limits():
            resource.setrlimit(resource.RLIMIT_AS,(c['address_space_limit_bytes'],)*2)
            resource.setrlimit(resource.RLIMIT_CPU,(cpu,)*2)
            resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        env=dict(os.environ,LD_LIBRARY_PATH=str(runtime),OMP_NUM_THREADS=str(c['threads']))
        start=time.monotonic()
        binding.update(command=command,address_space_limit_bytes=c['address_space_limit_bytes'],
                       cpu_seconds_limit=cpu,cgroup_memory_before_bytes=guard.cgroup_current())
        if binding['cgroup_memory_before_bytes'] is None or guard.cgroup_resident() is None:
            raise RuntimeError('required cgroup memory telemetry is unavailable')
        log_path.parent.mkdir(parents=True,exist_ok=True)
        with log_path.open('xb') as log:
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,preexec_fn=limits)
            binding['process_id']=process.pid
            stop_monitor=threading.Event()
            def monitor():
                while not stop_monitor.wait(.25):
                    current=guard.cgroup_current()
                    if current is not None:
                        binding['cgroup_memory_observed_peak_bytes']=max(current,binding.get('cgroup_memory_observed_peak_bytes',0))
                    resident=guard.cgroup_resident()
                    if current is None or resident is None:
                        binding['watchdog_stop_reason']='required cgroup memory telemetry disappeared'
                        process.terminate()
                        return
                    if resident is not None:
                        binding['cgroup_resident_observed_peak_bytes']=max(resident,binding.get('cgroup_resident_observed_peak_bytes',0))
                    if resident is not None and resident>c['cgroup_stop_threshold_bytes']:
                        binding['watchdog_stop_reason']='cgroup resident metric above frozen 7 GiB threshold'
                        process.terminate()
                        return
                    if time.monotonic()-start>c['wall_timeout_seconds_per_process']:
                        binding['watchdog_stop_reason']='frozen process wall deadline exceeded'
                        process.terminate()
                        return
            watcher=threading.Thread(target=monitor,daemon=True)
            watcher.start()
            try:
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f'llama-server exited with {process.returncode}; see {log_path.name}')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                            health=json.load(response)
                        if health.get('status')=='ok':
                            break
                    except OSError:
                        pass
                    if time.monotonic()-start>c['startup_timeout_seconds']:
                        raise TimeoutError('backend startup exceeded frozen wall limit')
                    time.sleep(.1)
                binding.update(startup_seconds=time.monotonic()-start,port=port,
                               cgroup_memory_loaded_bytes=guard.cgroup_current())
                resident=guard.cgroup_resident()
                if resident is not None and resident>c['cgroup_stop_threshold_bytes']:
                    raise MemoryError('cgroup resident metric exceeds frozen 7 GiB stop threshold')
                yield binding
            finally:
                stop_monitor.set()
                watcher.join(timeout=1)
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
                binding.update(elapsed_seconds=time.monotonic()-start,process_exit_code=process.returncode,
                               children_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                               cgroup_memory_after_bytes=guard.cgroup_current())


DEFAULT_SYSTEM_PROMPT = ('You are a careful document reader. Answer the question using only the supplied source excerpts. '
    'Distinguish what each document version says and preserve the relevant entity, reporting period, measure and selected sample. '
    'Do not recompute quantities or use outside knowledge. If the excerpts do not establish a requested fact, say so. '
    'Give a concise answer of at most 120 words, with supporting citations in the form [document_id p.N]. '
    'Use ordinary prose, not JSON.')


def build_prompt(question,context):
    require(isinstance(question,str) and question.strip(),'Nonempty question required.')
    require(isinstance(context,str),'Context must be text; empty context is allowed for budget accounting.')
    return ('Question:\n'+question+'\n\nSource excerpts:\n'+context+
            '\n\nAnswer the question using the supplied excerpts and cite source pages.')


def render_and_tokenize(port,question,context,c):
    """Identical formatting for corpus selection and final request construction."""
    return base.render_and_tokenize(port,messages({'prompt':build_prompt(question,context)},c))


class TokenizerBridge:
    def __init__(self,port,c,properties,asset_binding):
        self.port=port;self.config=c
        self.identity={'model_sha256':c['model']['sha256'],'model_revision':c['model']['revision'],
            'runtime_files_sha256':asset_binding['runtime_files_sha256'],
            'chat_template_sha256':hashlib.sha256(properties['chat_template'].encode()).hexdigest(),
            'chat_template_kwargs':{'enable_thinking':False},
            'config_sha256_canonical':base.canonical_hash(c),
            'prompt_builder_sha256':base.digest(Path(__file__))}

    def count_prompt(self,question,context,config=None):
        require(config is None or config==self.config,'Counter/native configuration differs.')
        prompt=render_and_tokenize(self.port,question,context,self.config)
        return {'input_tokens':prompt['input_tokens'],
                'rendered_prompt_sha256':prompt['rendered_prompt_sha256'],
                'token_ids_sha256':prompt['input_token_ids_sha256'],
                'fits_input_budget':prompt['input_tokens']<=self.config['maximum_input_tokens'],
                'fits_context_with_output_reserve':prompt['input_tokens']+256<=4096,
                'tokenizer_identity':self.identity}


@contextmanager
def tokenizer_session(config_path,external_log_directory):
    """One guarded native server; tokenization only, never completion."""
    c=config(config_path);folder=external_path(external_log_directory)
    require(not folder.exists(),'Tokenizer session already exists; use a separately recorded session.')
    folder.mkdir(parents=True);backend=ConfiguredBackend(c);assets=Path(c['model']['path']).parent
    binding=backend.verify_assets(assets)
    write(folder/'frozen_tokenizer.json',{'created_unix_seconds':time.time(),
        'config_sha256':base.digest(config_path),'collector_sha256':base.digest(Path(__file__)),
        'config':c,'asset_binding':binding,'completion_calls_authorized':0})
    trace={}
    try:
        with backend.server(assets,folder/'server.log',port=c['port'],trace=trace) as execution:
            properties=props(execution['port'],assets,folder/'props.json',backend)
            yield TokenizerBridge(execution['port'],c,properties,binding)
        require(trace.get('process_exit_code')==0 and not trace.get('watchdog_stop_reason'),'Tokenizer process failed.')
    finally:
        write(folder/'execution.json',trace)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['freeze','execute','verify'])
    parser.add_argument('--external',type=Path,required=True)
    for name in ['config','request','protocol','reference-review','data-dir','receipt']:
        parser.add_argument('--'+name,type=Path)
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

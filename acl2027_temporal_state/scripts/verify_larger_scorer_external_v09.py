#!/usr/bin/env python3
"""Optional exact-artifact comparison with user-supplied matching full inputs.

No network/model calls and no reference labels. This does not independently
rerun the tokenizer or regenerate model scores. Default checkpoint replay
does not require these externally retained source-bearing artifacts.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess

import larger_scorer_v09 as scorer


def tokenize(assets,model,prompt):
    c=scorer.config();spec=c if model=='large' else c['comparator']
    runtime=assets/c['runtime_directory']
    command=[str(runtime/'llama-tokenize'),'-m',str(assets/spec['model_file']),
             '--stdin','--ids','--no-escape','--offline']
    def limits():
        resource.setrlimit(resource.RLIMIT_AS,(1073741824,)*2)
        resource.setrlimit(resource.RLIMIT_CPU,(10,)*2)
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    response=subprocess.run(command,input=prompt.encode(),capture_output=True,
                            env=dict(os.environ,LD_LIBRARY_PATH=str(runtime)),
                            preexec_fn=limits,timeout=20,check=True)
    tokens=ast.literal_eval(response.stdout.decode())
    if not isinstance(tokens,list) or not all(type(x) is int for x in tokens):
        raise ValueError('tokenizer did not return integer IDs')
    return tokens,{'command':command,'stdout_sha256':hashlib.sha256(response.stdout).hexdigest(),
                   'stderr_sha256':hashlib.sha256(response.stderr).hexdigest(),
                   'returncode':response.returncode,'address_space_limit_bytes':1073741824,
                   'cpu_seconds_limit':10,'timeout_seconds':20}


def audit(request_path,external_dir,assets=None):
    directory=scorer.DATA/'research_attempt02'
    frozen=scorer.load(directory/'frozen_inputs.json')
    receipt=scorer.load(scorer.PROJECT/'results/larger_scorer_v09_receipt.json')
    if receipt['status']!='completed':
        raise ValueError('collection incomplete')
    if scorer.base.digest(request_path)!=frozen['external_request_sha256'] or request_path.stat().st_size!=frozen['external_request_bytes']:
        raise ValueError('external request differs from frozen bytes')
    actual_items=scorer.request_items(request_path)
    projection=[{'item_id':x['item_id'],'history_id':x['history_id'],'condition_id':x['condition_id'],
                 'messages_canonical_sha256':scorer.base.canonical_hash(x['messages'])} for x in actual_items]
    if projection!=frozen['request_projection']:
        raise ValueError('source-only message projection differs')
    source_items=scorer.load(request_path)['items']
    for index in range(0,len(actual_items),2):
        yes_no,no_yes=actual_items[index:index+2]
        if yes_no['messages'][0]!=no_yes['messages'][0]:
            raise ValueError('condition changes system instruction')
        a,b=(x['messages'][1]['content'] for x in (yes_no,no_yes))
        prefix_a,suffix_a=a.rsplit('\n\nLABELS\n',1)
        prefix_b,suffix_b=b.rsplit('\n\nLABELS\n',1)
        la,lb=suffix_a.splitlines(),suffix_b.splitlines()
        if prefix_a!=prefix_b or la[:2]!=list(reversed(lb[:2])) or la[2:]!=lb[2:]:
            raise ValueError('conditions differ beyond label-definition order')
        original=source_items[index//2]
        if not prefix_a.startswith('SOURCE_CONTEXT\n'+original['source_text']+'\n\nITEM\n'):
            raise ValueError('whole source input was changed')
    for name,spec in receipt['external_artifacts'].items():
        if name not in ('rendered_prompts.jsonl','raw_responses.jsonl'):
            raise ValueError('unexpected external artifact name')
        p=external_dir/name
        if p.stat().st_size!=spec['bytes'] or scorer.base.digest(p)!=spec['sha256']:
            raise ValueError('external artifact hash/size mismatch')
    def rows(path):
        return list(map(json.loads,path.read_text().splitlines()))
    prompts=rows(external_dir/'rendered_prompts.jsonl')
    originals=rows(external_dir/'raw_responses.jsonl')
    packaged_prompts=rows(directory/'rendered_prompts.jsonl')
    packaged_raw=rows(directory/'raw_responses.jsonl')
    if not all(len(x)==16 for x in (prompts,originals,packaged_prompts,packaged_raw)):
        raise ValueError('external record coverage mismatch')
    tokenization_rows=[]
    if assets is not None:
        scorer.verify_assets(assets)
        c=scorer.config();scorer.fetch.verify_asset(assets/c['comparator']['model_file'],c['comparator']['asset'])
        source=scorer.DATA/'tokenizer_vocab_only_pinned.cpp.txt'
        if scorer.base.digest(source)!='db0cd035294b91009250029a1435a1a688d72eb489c406b630d2293b30d6fb34':
            raise ValueError('pinned tokenizer source evidence differs')
        audit_external=assets/'tokenizer_audit_external'
        audit_external.mkdir(exist_ok=False)
    for index,(full_prompt,full_raw,pp,pr) in enumerate(zip(prompts,originals,packaged_prompts,packaged_raw)):
        prompt=full_prompt['prompt']
        if scorer.base.canonical_hash(prompt['input_token_ids'])!=prompt['input_token_ids_sha256']:
            raise ValueError('full token array hash mismatch')
        if hashlib.sha256(prompt['rendered_prompt'].encode()).hexdigest()!=prompt['rendered_prompt_sha256']:
            raise ValueError('full prompt text hash mismatch')
        expected_prompt={k:v for k,v in full_prompt.items() if k!='prompt'}|{'prompt':scorer.projected_prompt(prompt)}
        if expected_prompt!=pp:
            raise ValueError('packaged prompt projection changed')
        expected_raw={k:v for k,v in full_raw.items() if k not in ('request','response')}|{
            'request':scorer.projected_request(full_raw['request']),
            'response':scorer.projected_response(full_raw['response'],prompt)}
        if expected_raw!=pr or full_raw['process_index']!=index:
            raise ValueError('packaged probability/request projection changed')
        if scorer.extract(prompt,full_raw['request'],full_raw['response'])!=scorer.extract(pp['prompt'],pr['request'],pr['response']):
            raise ValueError('projection altered a derived measurement')
        if assets is not None:
            for suffix in ('','Yes','No'):
                tokens,execution=tokenize(assets,full_prompt['model_key'],prompt['rendered_prompt']+suffix)
                expected=prompt['input_token_ids']+([] if not suffix else prompt['label_tokens'][suffix])
                if tokens!=expected:
                    raise ValueError('independent vocabulary-only tokenization boundary mismatch')
                key=f'{index:02d}_{suffix or "prefix"}'
                scorer.base.write_json(audit_external/(key+'.json'),{'tokens':tokens,'execution':execution})
                tokenization_rows.append({'process_index':index,'model_key':full_prompt['model_key'],
                    'item_id':full_prompt['item_id'],'condition_id':full_prompt['condition_id'],
                    'continuation':suffix,'tokens':len(tokens),'canonical_token_ids_sha256':scorer.base.canonical_hash(tokens),
                    'exact_prefix_plus_one_label':bool(suffix),'execution':execution})
    output={'schema_version':'larger_scorer_external_artifact_audit_v0.9',
            'status':'passed_exact_record_and_projection_comparison','research_rows_checked':16,
            'source_message_conditions_checked':8,'new_model_calls':0,
            'whole_source_inputs_unchanged':True,'condition_only_label_definition_order_changes':True,
            'independent_tokenizer_regeneration':assets is not None,'independent_score_regeneration':False,
            'tokenization_checks':len(tokenization_rows),'tokenization_records':tokenization_rows,
            'new_forward_pass_completions':0,
            'matched_models_same_rendered_prompt_bytes':all(prompts[i]['prompt']['rendered_prompt']==prompts[i+8]['prompt']['rendered_prompt'] for i in range(8)),
            'matched_models_same_input_token_ids':all(prompts[i]['prompt']['input_token_ids']==prompts[i+8]['prompt']['input_token_ids'] for i in range(8)),
            'request_sha256':scorer.base.digest(request_path),
            'external_artifacts':receipt['external_artifacts'],
            'collection_receipt_sha256':scorer.base.digest(scorer.PROJECT/'results/larger_scorer_v09_receipt.json'),
            'claim_limit':'Checks matching retained source-bearing records against packaged projections; optional pinned vocabulary-only CLI independently rechecks exact token sequences/label boundaries. It does not regenerate likelihoods, use independent human source labels, or establish general tokenizer correctness.'}
    if assets is not None:
        output['tokenizer_source_evidence']={'url':'https://raw.githubusercontent.com/ggml-org/llama.cpp/7fe450e19305b828c199d602c23a8337aaa1f03b/tools/tokenize/tokenize.cpp',
            'sha256':scorer.base.digest(source),'vocab_only_explicitly_enabled':True,'llama_decode_or_encode_calls_in_main_source':False}
        output['external_tokenizer_records_sha256']={p.name:scorer.base.digest(p) for p in sorted(audit_external.iterdir())}
    scorer.base.write_json(scorer.PROJECT/'results/larger_scorer_v09_external_audit.json',output)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--external-dir',type=Path,required=True)
    p.add_argument('--assets',type=Path,help='Also independently retokenize via pinned vocabulary-only CLI; no score computations.')
    a=p.parse_args()
    result=audit(a.request,a.external_dir,a.assets)
    print(json.dumps({k:v for k,v in result.items() if k not in ('tokenization_records','external_tokenizer_records_sha256')}))

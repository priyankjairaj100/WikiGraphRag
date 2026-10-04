#!/usr/bin/env python3
"""Freeze new v18 populations using the unchanged, qualified v17.2 renderer."""
from __future__ import annotations
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
BASE='data/reader_binding_v17/source_render_identity_protocol_v17_2.json'
INVENTORY='results/pre_author_bundle_inventory_v17.json'
INVENTORY_SHA='b912a697a6ce1635fbd66ab7f7201fa69084f83b1e60a4ba585870d1ef02fe1b'
IDENTITY_RESULT='results/source_render_identity_execution_v17_2.json'
IDENTITY_RESULT_SHA='8a4cdfec31e8cc6ca1bef7756fc83fb0318922b9f9450da7fba99aeace2e7207'
NVDA_ANCHOR={'byte_start':142881,'byte_stop':143460,
             'span_sha256':'5cccc615229ca686949d85bb33474f1f1e8967bab071ce3e037e887094b1a13c'}
SOURCE_FIELDS=('source_path','external_filename','source_sha256','source_bytes')


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def load(path):return json.loads((ROOT/path).read_text())


def require_hash(path,expected):
    if digest(ROOT/path)!=expected:raise ValueError('frozen_input_hash_mismatch:'+path)


def dependency_population(base):
    require_hash(INVENTORY,INVENTORY_SHA)
    inventory=load(INVENTORY)
    blocks=inventory['additional_dependency_blocks']
    if len(blocks)!=40 or inventory['additional_dependency_block_count']!=40:
        raise ValueError('dependency_denominator_mismatch')
    if inventory['protocol_sha256']!=digest(ROOT/'data/reader_binding_v17/pre_author_bundle_protocol.json'):
        raise ValueError('bundle_protocol_binding_mismatch')
    grouped,seen={},set()
    for block in blocks:
        i=block['source_index'];original=base['sources'][i]
        if any(block[k]!=original[k] for k in SOURCE_FIELDS):raise ValueError('dependency_source_identity_mismatch')
        key=(i,block['block_id'])
        if key in seen:raise ValueError('duplicate_dependency_block')
        seen.add(key)
        source=grouped.setdefault(i,{**{k:original[k] for k in SOURCE_FIELDS},
                                     'original_source_index':i,'blocks':[]})
        item=copy.deepcopy({k:block[k] for k in ['block_id','kind','dom_path','anchor','roles','candidate_bundles']})
        item['role']='additional_dependency'
        source['blocks'].append(item)
    if set(grouped)!=set(range(11)) or Counter(b['kind'] for b in blocks)!=Counter(paragraph=36,table_row=2,table=2):
        raise ValueError('dependency_source_or_kind_population_mismatch')
    sources=[grouped[i] for i in sorted(grouped)]
    for source in sources:source['blocks'].sort(key=lambda b:(b['anchor']['byte_start'],b['block_id']))
    if [len(s['blocks']) for s in sources]!=[10,9,4,2,1,2,4,4,1,2,1]:
        raise ValueError('dependency_per_source_count_mismatch')
    metadata={'selection':'all_exact_40_previously_frozen_additional_dependencies_no_additions_or_substitutions',
              'kind_counts':{'paragraph':36,'table_row':2,'table':2},
              'selected_answer_table_population_unchanged':True,
              'raw_fragment_byte_total':sum(b['anchor']['byte_stop']-b['anchor']['byte_start'] for b in blocks),
              'scope':'Additional dependency inspection only; no complete-evidence or question/reference admission.'}
    inputs=[INVENTORY,'data/reader_binding_v17/pre_author_bundle_protocol.json',
            'data/reader_binding_v17/bundle_dependency_plan.json']
    return sources,metadata,inputs


def nvda_population(base):
    require_hash(IDENTITY_RESULT,IDENTITY_RESULT_SHA)
    result=load(IDENTITY_RESULT)
    if result['protocol_sha256']!=digest(ROOT/BASE) or (result['requested_blocks'],result['rendered_blocks'],result['retained_failed_or_unrenderable_blocks'])!=(34,33,1):
        raise ValueError('original_identity_attempt_binding_mismatch')
    source=base['sources'][4]
    block=next(b for b in source['blocks'] if b['block_id']=='identity_142881')
    if block['anchor']!=NVDA_ANCHOR or block['identity_fields']!=['DocumentType'] or source['external_filename']!='NVDA_2022.html':
        raise ValueError('fixed_NVDA_identity_mismatch')
    failed=[(i,b) for i,s in enumerate(result['sources']) for b in s['blocks'] if b['status']!='rendered_pending_full_visual_and_css_review']
    if len(failed)!=1 or failed[0][0]!=4:raise ValueError('original_identity_failure_population_mismatch')
    prior=failed[0][1]
    if prior['block_id']!=block['block_id'] or prior['anchor']!=NVDA_ANCHOR or prior['status']!='stopped_at_process_group_telemetry_unavailable':
        raise ValueError('original_identity_failure_mismatch')
    conversion=prior['conversion']
    if conversion['owned_group_cleanup']['cleanup_confirmed'] is not True or conversion['owned_group_cleanup']['status']!='group_absent':
        raise ValueError('original_worker_cleanup_unconfirmed')
    if not any(s.get('error_code')=='live_process_rss_missing' for s in conversion['process_group_samples']):
        raise ValueError('original_telemetry_failure_reason_mismatch')
    selected={**{k:source[k] for k in SOURCE_FIELDS},'original_source_index':4,
              'blocks':[copy.deepcopy(block)]}
    metadata={'selection':'post_failure_selected_exactly_one_NVDA_2022_DocumentType_block',
              'original_identity_attempt':{'requested_blocks':34,'rendered_blocks':33,'failed_blocks':1},
              'original_failure_status':prior['status'],'original_result_relabeling_permitted':False,
              'raw_fragment_byte_total':579,
              'scope':'One separately counted telemetry recovery; no identity-card admission from conversion.'}
    return [selected],metadata,[IDENTITY_RESULT,'results/source_render_identity_summary_v17_2.json']


def check_source_anchors(sources,directory):
    directory=Path(directory).resolve()
    for source in sources:
        if not 1<=len(source['blocks'])<=16 or source['source_bytes']>25*1024**2:
            raise ValueError('source_or_population_bound')
        path=(directory/source['external_filename']).resolve()
        if directory not in path.parents or path.stat().st_size!=source['source_bytes'] or digest(path)!=source['source_sha256']:
            raise ValueError('source_file_binding_mismatch')
        raw=path.read_bytes()
        for block in source['blocks']:
            a=block['anchor'];start,stop=a['byte_start'],a['byte_stop']
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',block['block_id']) or not 0<=start<stop<=len(raw):
                raise ValueError('block_identity_or_span_bound')
            if stop-start>5*1024**2 or hashlib.sha256(raw[start:stop]).hexdigest()!=a['span_sha256']:
                raise ValueError('block_source_span_mismatch')
        del raw


def prepare(mode,sources_directory,output):
    base=load(BASE)
    if base['schema_version']!='source_block_lo_inspection_v17_2' or base['mode']!='identity' or len(base['sources'])!=12 or base['max_pages']!=8:
        raise ValueError('unchanged_renderer_profile_required')
    for group in ('code_bindings','input_bindings'):
        for path,expected in base[group].items():require_hash(path,expected)
    for path,expected in base['runtime_bindings'].items():
        if digest(path)!=expected:raise ValueError('unchanged_runtime_binding_mismatch')
    sources,metadata,extra_inputs=(dependency_population(base) if mode=='dependencies' else nvda_population(base))
    check_source_anchors(sources,sources_directory)
    name='dependencies' if mode=='dependencies' else 'nvda_identity_recovery'
    planned_external='/dev/shm/wikigraph_v15/external/source_render_'+name+'_v18/attempt01'
    planned_result='results/source_render_'+name+'_execution_v18.json'
    if Path(planned_external).exists() or (ROOT/planned_result).exists():raise ValueError('planned_execution_output_exists')
    protocol=copy.deepcopy(base)
    protocol.update(frozen_at_utc=datetime.now(timezone.utc).isoformat(),amendment_version='v18',
        mode=mode,sources=sources,source_denominator=len(sources),
        block_denominator=sum(len(s['blocks']) for s in sources),metadata=metadata,
        declaration_timing='after_v17_checkpoint_before_v18_rendering_or_questions',
        parent_git_checkpoint='f77f70c51b12c935ccf897ba64bf19501d44c427',
        unchanged_implementation_profile='v17.2_qualified_inline_rgba_inspection',
        planned_external_output=planned_external,planned_public_result=planned_result,
        source_assembly_contract='Exact fragment with complete ancestor opening-tag chain and source head styles; no ancestor siblings or answer-row reconstruction; actual 5MiB assembly/resource/CSS checks remain execution gates.',
        unchanged_limits={'worker_count':1,'reserve_bytes':512*1024**2,'owned_group_rss_bytes':512*1024**2,
          'proxy_bytes':7*1024**3,'kernel_max_bytes':8*1024**3,'conversion_seconds':300,'raster_seconds':60,
          'page_cap':8,'sentinel_page':9,'block_output_bytes':20_000_000,'attempt_output_bytes':150_000_000,
          'cleanup_failure_stops_further_workers':True,'automatic_additional_retries_permitted':0},
        automatic_additional_retries_permitted=0,natural_QA_predictions=0,model_calls=0,
        author_release_allowed=False,identity_admission_performed=False,complete_evidence_admitted=0)
    protocol['code_bindings']['scripts/prepare_source_render_populations_v18.py']=digest(__file__)
    protocol['input_bindings'].update({p:digest(ROOT/p) for p in [BASE,*extra_inputs]})
    target=Path(output).resolve()
    if ROOT not in target.parents or target.exists():raise ValueError('protocol_output_must_be_new_inside_project')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x') as stream:json.dump(protocol,stream,sort_keys=True,indent=2);stream.write('\n')
    return protocol


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',required=True,choices=['dependencies','nvda-identity-recovery'])
    p.add_argument('--sources',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();r=prepare(a.mode,a.sources,a.output)
    print(json.dumps({'mode':a.mode,'sources':r['source_denominator'],'blocks':r['block_denominator'],'protocol_sha256':digest(a.output)}))

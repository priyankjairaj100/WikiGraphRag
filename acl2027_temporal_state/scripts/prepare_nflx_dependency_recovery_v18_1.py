#!/usr/bin/env python3
"""Prepare, but never execute, the fixed one-block NFLX telemetry recovery draft."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PARENT_PROTOCOL='data/reader_binding_v18/source_render_dependencies_protocol_v18.json'
PARENT_PROTOCOL_SHA='0af419d76331858e7a469a83ac1028db40dd0ffbd9f19d642d78d89c3e7fcf66'
PARENT_RESULT='results/source_render_dependencies_execution_v18.json'
PARENT_RESULT_SHA='502db820bf8bc575cf371141d42f7e78395dc31ab2ede3c87482d1664e5c453c'
ANCHOR={'byte_start':988034,'byte_stop':988413,
        'span_sha256':'4a5fd3926e0a47d40e7bd5ac49c0a0e469c69d7f4402ea1705fa17f852311406'}


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def prepare(sources_directory,output):
    for name,expected in [(PARENT_PROTOCOL,PARENT_PROTOCOL_SHA),(PARENT_RESULT,PARENT_RESULT_SHA)]:
        if digest(ROOT/name)!=expected:raise ValueError('parent_binding_mismatch')
    parent=json.loads((ROOT/PARENT_PROTOCOL).read_text())
    result=json.loads((ROOT/PARENT_RESULT).read_text())
    if result['protocol_sha256']!=PARENT_PROTOCOL_SHA or (result['requested_blocks'],result['rendered_blocks'],result['retained_failed_or_unrenderable_blocks'])!=(40,39,1):
        raise ValueError('parent_denominator_mismatch')
    for group in ['code_bindings','input_bindings']:
        for name,expected in parent[group].items():
            if digest(ROOT/name)!=expected:raise ValueError('unchanged_binding_mismatch')
    for name,expected in parent['runtime_bindings'].items():
        if digest(name)!=expected:raise ValueError('unchanged_runtime_binding_mismatch')
    source=next(s for s in parent['sources'] if s['original_source_index']==3)
    block=next(b for b in source['blocks'] if b['block_id']=='paragraph_746')
    if source['external_filename']!='NFLX_2023.html' or block['anchor']!=ANCHOR or block['roles']!=['regional_metric_caption'] or block['candidate_bundles']!=['p03t37','p03t38']:
        raise ValueError('fixed_recovery_identity_mismatch')
    failures=[(s,b) for s in result['sources'] for b in s['blocks'] if b['status']!='rendered_pending_full_visual_and_css_review']
    if len(failures)!=1:raise ValueError('parent_failure_population_mismatch')
    previous_source,entry=failures[0]
    if previous_source['source_path']!=source['source_path'] or entry['block_id']!=block['block_id'] or entry['anchor']!=ANCHOR or entry['status']!='stopped_at_process_group_telemetry_unavailable':
        raise ValueError('original_failure_identity_mismatch')
    conversion=entry['conversion']
    if conversion['owned_group_cleanup'].get('cleanup_confirmed') is not True or conversion['owned_group_cleanup']['status']!='group_absent':
        raise ValueError('prior_cleanup_not_confirmed')
    if not any(s.get('error_code')=='live_process_rss_missing' for s in conversion['process_group_samples']):
        raise ValueError('original_stop_reason_mismatch')
    if entry['resource_check']['status']!='passive_resource_profile_passed' or entry['compatibility']['patch_count']!=0:
        raise ValueError('original_source_preparation_profile_mismatch')
    if conversion['baseline_snapshot']['events']!=conversion['final_snapshot']['events']:
        raise ValueError('original_attempt_had_OOM_event_change')
    directory=Path(sources_directory).resolve();path=(directory/source['external_filename']).resolve()
    if directory not in path.parents or path.stat().st_size!=source['source_bytes'] or digest(path)!=source['source_sha256']:
        raise ValueError('original_source_bytes_changed')
    with path.open('rb') as stream:
        stream.seek(ANCHOR['byte_start']);fragment=stream.read(ANCHOR['byte_stop']-ANCHOR['byte_start'])
    if hashlib.sha256(fragment).hexdigest()!=ANCHOR['span_sha256']:raise ValueError('exact_fragment_changed')
    selected=copy.deepcopy(source);selected['blocks']=[copy.deepcopy(block)]
    draft=copy.deepcopy(parent);draft.pop('frozen_at_utc',None)
    draft.update(amendment_version='v18.1',mode='nflx-single-dependency-telemetry-recovery',
        protocol_status='draft_pending_independent_readback_and_root_freeze_approval',
        prepared_at_utc=datetime.now(timezone.utc).isoformat(),
        declaration_timing='after_original_40_block_attempt_before_this_recovery_execution',
        source_denominator=1,block_denominator=1,sources=[selected],
        planned_external_output='/dev/shm/wikigraph_v15/external/source_render_nflx_dependency_recovery_v18_1/attempt01',
        planned_public_result='results/source_render_nflx_dependency_recovery_execution_v18_1.json',
        metadata={'selection':'post_failure_selected_exactly_original_source03_paragraph_746_once',
            'candidate_bundles':['p03t37','p03t38'],'raw_fragment_bytes':379,
            'original_dependency_attempt':{'requested_blocks':40,'rendered_blocks':39,'failed_blocks':1},
            'original_result_relabeling_permitted':False,
            'recovery_eligibility_basis':{'original_stop':'live_process_rss_missing',
                'passive_resource_profile_passed':True,'rgba_patches':0,'OOM_event_delta':0,
                'owned_group_cleanup':'group_absent','success_not_guaranteed':True},
            'original_preparation_bindings':{k:entry[k] for k in ['ancestor_openings','head_stylesheets','source_assembly_sha256','render_html_sha256']},
            'scope':'One separately counted renderer telemetry recovery only; no selector expansion, source search, identity or complete-evidence admission.'},
        automatic_additional_retries_permitted=0,author_release_allowed=False,
        natural_QA_predictions=0,model_calls=0,complete_evidence_admitted=0)
    draft['code_bindings']['scripts/prepare_nflx_dependency_recovery_v18_1.py']=digest(__file__)
    draft['input_bindings'].update({PARENT_PROTOCOL:PARENT_PROTOCOL_SHA,PARENT_RESULT:PARENT_RESULT_SHA,
        'results/source_render_dependencies_summary_v18.json':digest(ROOT/'results/source_render_dependencies_summary_v18.json')})
    target=Path(output).resolve()
    if ROOT not in target.parents or target.exists():raise ValueError('new_project_draft_path_required')
    if Path(draft['planned_external_output']).exists() or (ROOT/draft['planned_public_result']).exists():
        raise ValueError('planned_attempt_output_exists')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x') as stream:json.dump(draft,stream,sort_keys=True,indent=2);stream.write('\n')
    return draft


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sources',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();r=prepare(a.sources,a.output)
    print(json.dumps({'status':r['protocol_status'],'sources':1,'blocks':1,'draft_sha256':digest(a.output)}))

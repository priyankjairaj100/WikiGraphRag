#!/usr/bin/env python3
"""Freeze separate authored, eight-block recovery and 34-block identity populations."""
import argparse
import copy
import json
from pathlib import Path
import render_source_blocks_lo_v17_2 as lo


def prepare(mode, output):
    root=lo.ROOT
    original_path=root/'data/reader_binding_v17/source_render_natural_protocol_v17.json'
    original=json.loads(original_path.read_text())
    code_paths=['scripts/render_source_blocks_lo_v17_2.py','scripts/css_rgba_compat_v17_2.py',
        'scripts/prepare_compatibility_render_v17_2.py','scripts/cgroup_guard_v16_2.py',
        'scripts/process_group_rss_v16.py','scripts/probe_source_render_lo_v16.py',
        'scripts/render_source_blocks_v16.py','src/temporal_state/typed_reader_v15_1.py',
        'tests/test_css_rgba_compat_v17_2.py','tests/test_css_rgba_compat_review_v17_2.py',
        'tests/test_source_render_rgba_consumer_v17_2.py','docs/source_render_rgba_compatibility_contract_v17_2.txt',
        'scripts/index_source_render_v17_2.py']
    inputs=['data/reader_binding_v17/source_render_natural_protocol_v17.json',
            'results/source_render_rgba_diagnosis_v17.json',
            'results/source_render_rgba_code_review_v17_2.json',
            'results/owned_rss_audit_closure_v17.json','results/source_render_code_review_v17.json']
    review=json.loads((root/inputs[2]).read_text())
    if review.get('execution_gate_closed') is not True:
        raise ValueError('compatibility_code_review_gate_not_closed')
    for path,digest in review['input_bindings'].items():
        if lo.source_render.digest(root/path)!=digest:
            raise ValueError('reviewed_code_binding_mismatch')
    metadata={}
    if mode=='authored':
        p=root/'data/reader_binding_v17/rgba_compatibility_authored_fixture_v17_2.html'
        raw=p.read_bytes()
        node=next(n for n in lo.source_render.byte_reader._parse(raw) if n.tag==lo.source_render.XH+'div')
        sources=[{'source_path':'authored/rgba_compatibility_authored_fixture_v17_2.html',
            'external_filename':p.name,'source_sha256':lo.source_render.sha(raw),'source_bytes':len(raw),
            'blocks':[{'block_id':'authored_contrast_and_merges','dom_path':node.path,
                       'anchor':lo.source_render.bound_anchor(raw,node),'role':'authored_control'}]}]
        inputs.append(str(p.relative_to(root)))
        metadata={'expected_pages':1,'natural_sources_rendered':0,
            'required_visual_checks':['opaque_foreground','transparent_background_on_blue',
             'white_text_on_opaque_black_background','existing_percent_alpha',
             'unchanged_one_percent_border','merged_year_and_numeric_columns','source_text_unchanged']}
    else:
        inputs += ['results/source_render_rgba_compatibility_authored_execution_v17_2.json',
                   'results/source_render_rgba_compatibility_visual_qa_v17_2.json']
        visual=json.loads((root/inputs[-1]).read_text())
        if visual.get('visual_control_passed') is not True:
            raise ValueError('authored_visual_gate_not_closed')
        if mode=='natural-recovery':
            admission_path=root/'results/source_render_admission_root_06_11_v17.json'
            admission=json.loads(admission_path.read_text())
            inspected=next(s for s in admission['sources'] if s['source_index']==9)
            chosen=[0,1,2,3,4,7,8,9]
            selected=[]
            source=copy.deepcopy(original['sources'][9])
            for i in chosen:
                block=copy.deepcopy(source['blocks'][i])
                prior_review=next(b for b in inspected['blocks'] if b['block_id']==block['block_id'])
                if prior_review['decision']!='excluded':
                    raise ValueError('recovery_member_not_original_visual_failure')
                block['original_block_index']=i
                selected.append(block)
            source.update(blocks=selected,original_source_index=9)
            sources=[source]
            inputs += ['results/source_render_admission_root_06_11_v17.json',
                       'results/source_render_natural_execution_v17.json',
                       'results/source_render_admission_aggregate_v17.json']
            metadata={'population_selection':'post_visual_failure_selected_exactly_eight_nonblank_source09_blocks',
                      'original_source_index':9,'original_block_indices':chosen,
                      'original_empty_blocks_not_retried':[5,6],
                      'original_execution_and_admission_outcomes_unchanged':True}
        elif mode=='identity':
            p=root/'results/identity_dependency_candidates_v17.json'
            candidates=json.loads(p.read_text())
            sources=copy.deepcopy(candidates['sources'])
            if len(sources)!=12 or sum(len(s['blocks']) for s in sources)!=34:
                raise ValueError('identity_population_mismatch')
            inputs += ['results/identity_dependency_candidates_v17.json',
                       'data/reader_binding_v17/identity_dependency_selection_protocol.json',
                       'results/identity_dependency_code_review_v17.json']
            metadata={'population_selection':'all_34_frozen_identity_candidate_blocks_no_substitutions',
                      'required_identity_fields':36,'fields_with_candidates':34,
                      'missing_issuer_witnesses_preserved':['SLB_2022','SLB_2023'],
                      'identity_admission_performed':False}
        else:raise ValueError('unknown_mode')
    for path,digest in original['runtime_bindings'].items():
        if lo.source_render.digest(path)!=digest:raise ValueError('runtime_mismatch')
    protocol={'schema_version':lo.SCHEMA,'frozen_at_utc':lo.source_render.utc(),
        'amendment_version':'v17.2','mode':mode,'metadata':metadata,
        'code_bindings':{p:lo.source_render.digest(root/p) for p in code_paths},
        'input_bindings':{p:lo.source_render.digest(root/p) for p in inputs},
        'runtime_bindings':original['runtime_bindings'],
        'resource_policy':'unchanged_v17_approved_small_process_limits_and_cleanup',
        'source_denominator':len(sources),'block_denominator':sum(len(s['blocks']) for s in sources),
        'max_pages':1 if mode=='authored' else original['max_pages'],'sources':sources,
        'automatic_additional_retries_permitted':0,'natural_QA_predictions':0,'model_calls':0,
        'admission':'All artifacts remain pending separate full source/CSS/raster review; no identity, question or reference admission from conversion.'}
    lo.write_json(Path(output),protocol)
    return protocol


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',required=True,choices=['authored','natural-recovery','identity']);p.add_argument('--output',required=True)
    a=p.parse_args();r=prepare(a.mode,a.output)
    print(json.dumps({'mode':r['mode'],'sources':r['source_denominator'],'blocks':r['block_denominator'],
                      'protocol_sha256':lo.source_render.digest(a.output)}))

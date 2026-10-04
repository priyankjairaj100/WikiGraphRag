#!/usr/bin/env python3
"""Freeze the separate four-block literal SLB cover supplement, without DEI equivalence."""
import argparse
import copy
import json
from pathlib import Path
import render_source_blocks_lo_v17_2 as lo

FOLLOWUP_SHA256='4e1b0581ac65acfd631d79a4d2e509012ad7244863348fdac4e66f5d038c510e'


def prepare(base_protocol, output):
    base_path=Path(base_protocol).resolve()
    base=json.loads(base_path.read_text())
    if base.get('schema_version')!=lo.SCHEMA or base.get('mode')!='identity' or base.get('block_denominator')!=34:
        raise ValueError('reviewed_identity_profile_required')
    for group in ('code_bindings','input_bindings'):
        for path,digest in base[group].items():
            if lo.source_render.digest(lo.ROOT/path)!=digest:raise ValueError('base_binding_mismatch')
    for path,digest in base['runtime_bindings'].items():
        if lo.source_render.digest(path)!=digest:raise ValueError('runtime_binding_mismatch')
    followup_path=lo.ROOT/'results/slb_literal_cover_followup_v17.json'
    if lo.source_render.digest(followup_path)!=FOLLOWUP_SHA256:raise ValueError('approved_followup_hash_mismatch')
    followup=json.loads(followup_path.read_text())
    if {s['source_index'] for s in followup['sources']}!={10,11}:raise ValueError('SLB_source_population_mismatch')
    sources=[]
    for source in followup['sources']:
        blocks=copy.deepcopy(source['blocks'])
        if len(blocks)!=2 or [b['role'] for b in blocks]!=['literal_cover_issuer_name','adjacent_charter_name_label']:
            raise ValueError('literal_name_and_label_pair_mismatch')
        if not blocks[0]['dom_path'].endswith('p[19]') or not blocks[1]['dom_path'].endswith('p[20]'):
            raise ValueError('fixed_cover_locator_mismatch')
        item={k:source[k] for k in ('source_path','external_filename','source_sha256','source_bytes')}
        item.update(blocks=blocks,original_source_index=source['source_index'])
        sources.append(item)
    protocol=copy.deepcopy(base)
    protocol.update(frozen_at_utc=lo.source_render.utc(),amendment_version='v17.3_literal_cover_supplement',
        mode='literal-cover-supplement',source_denominator=2,block_denominator=4,sources=sources,
        metadata={'population_selection':'exactly_p19_literal_name_and_p20_adjacent_charter_label_in_each_SLB_source',
                  'literal_visible_cover_wording_only':True,'hidden_DEI_value_replacement':False,
                  'legal_entity_equivalence_claimed':False,'original_identity_outcome_unchanged':'34_of_36_fields_with_candidates',
                  'separate_render_and_visual_admission_denominator':4,'identity_admission_performed':False})
    protocol['code_bindings']['scripts/prepare_slb_cover_render_v17_3.py']=lo.source_render.digest(__file__)
    protocol['input_bindings'][str(base_path.relative_to(lo.ROOT))]=lo.source_render.digest(base_path)
    protocol['input_bindings']['results/slb_literal_cover_followup_v17.json']=FOLLOWUP_SHA256
    lo.write_json(Path(output),protocol)
    return protocol


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-protocol',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();r=prepare(a.base_protocol,a.output)
    print(json.dumps({'sources':2,'blocks':4,'protocol_sha256':lo.source_render.digest(a.output)}))

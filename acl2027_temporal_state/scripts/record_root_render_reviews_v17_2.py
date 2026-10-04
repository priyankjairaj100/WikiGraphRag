"""Bind retained manual observations to source/CSS audits; never infer visual review.

Requires the separately saved private, actual root observations. This records
the three completed review populations, not a reusable automatic view grader.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def binding(p):
    return {'filename': p.name, 'bytes': p.stat().st_size, 'sha256': digest(p)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--external',type=Path,required=True)
    args=ap.parse_args()
    ext=args.external.resolve()
    observations=ext/'source_render_review_v17/root_compatibility_visual/visual_observations.json'
    obs=json.loads(observations.read_text())['rows']
    jobs=[
      ('contrast','source_render_rgba_recovery_v17_2','rgba_recovery_binding_v17_2','source_render_rgba_recovery','v17_2',8),
      ('identity','source_render_identity_v17_2','identity_compatibility_binding_v17_2','source_render_identity','v17_2',16),
      ('slb','source_render_slb_cover_v17_3','slb_cover_binding_v17_3','source_render_slb_cover','v17_3',4)]
    for kind,run,review,stem,version,count in jobs:
        index_path=ext/run/'attempt01/review_index.json'
        index=json.loads(index_path.read_text())
        paths=[f'data/reader_binding_v17/{stem}_protocol_{version}.json',
               f'results/{stem}_execution_{version}.json',
               f'results/{stem}_binding_review_{version}.json',
               'docs/source_render_admission_contract_v17.txt',
               'docs/source_render_rgba_compatibility_contract_v17_2.txt',
               'results/source_render_rgba_compatibility_visual_qa_v17_2.json']
        if kind=='identity': paths.append('data/reader_binding_v17/identity_render_review_plan_v17_2.json')
        result={'schema_version':'root_qualified_source_view_review_v17_2','reviewer':'root, model-role actual source and raster inspection',
                'reviewed_at_utc':datetime.now(timezone.utc).isoformat(),
                'input_bindings':{p:digest(ROOT/p) for p in paths},
                'private_review_index':binding(index_path),'private_actual_observations':binding(observations),
                'scope':{'qualified_inspection_only':True,'identity_cards_constructed':False,'browser_fidelity_claimed':False,
                         'border_opacity_fidelity_claimed':False,'semantic_attachment_verified':False,
                         'legal_entity_equivalence_claimed':False,'complete_evidence_packs':0,'questions_authored':0,'model_calls':0},'sources':[]}
        for s in index['sources']:
            si=s['source_index']
            if kind=='identity' and si<6: continue
            src={k:s[k] for k in ['source_index','source_path','source_sha256']}
            src['original_source_index']=s.get('original_source_index',si)
            src['blocks']=[]
            for b in s['blocks']:
                bi=b['block_index']
                o=[o for o in obs if (o['population'],o['source_index'],o['block_index'])==(kind,si,bi)]
                assert len(o)==1
                o=o[0]
                assert o['actual_raster_pages_viewed']==[p['sha256'] for p in b['pngs']]
                assert o['readable'] and not o['clipping_overlap'] and not o['material_border_grouping_ambiguity']
                assert o['source_pdf_whitespace_insensitive_equal']
                assert o.get('literal_equals_source',True) and o.get('manual_occurrence_sequence_exact',True)
                worksheet=ext/'source_render_review_v17'/review/f'source_{si:02d}'/f'block_{bi:02d}'/'worksheet.json'
                w=json.loads(worksheet.read_text())
                assert all(w['checks'].values()) and b['status']=='rendered_pending_full_visual_and_css_review'
                assert w['anchor']==b['anchor']
                assert not any(n['non_whitespace_text_characters'] or n['source_numeric_nodes'] for n in w['source_css']['hiding_nodes'])
                deps=w['public_checks']['source_dependency_counts']
                assert all(deps[k]==0 for k in ['base_elements','head_style_elements','out_of_head_style_elements','script_elements','stylesheet_links'])
                artifacts=[]
                for name,a in b['artifacts'].items():
                    p=Path(a['path']);assert digest(p)==a['sha256'] and p.stat().st_size==a['bytes']
                    artifacts.append(dict(filename=name,bytes=a['bytes'],sha256=a['sha256']))
                for a in b['pngs']:
                    p=Path(a['path']);assert digest(p)==a['sha256']
                    artifacts.append(dict(filename='raster/'+p.name,bytes=a['bytes'],sha256=a['sha256']))
                row={'block_id':b['block_id'],'role':b['role'],'dom_path':b['dom_path'],'anchor':b['anchor'],
                     'render_status':b['status'],'decision':'admitted_as_qualified_inspection_view',
                     'raster_pages_available':len(b['pngs']),'raster_page_indices_inspected':list(range(len(b['pngs']))),
                     'checks':{'source_identity_and_fragment':'pass','assembly_and_render_input':'pass','exact_compatibility_ledger':'pass',
                               'source_css_dependencies':'pass','all_pages_visually_inspected':'pass','text_glyphs_and_clipping':'pass',
                               'row_cell_span_and_numeric_mapping':'pass' if b['role']=='selected_table' else 'not_applicable',
                               'in_block_labels_and_markers':'pass','material_border_grouping_ambiguity':'absent_in_inspected_view'},
                     'reviewed_artifacts':artifacts,'private_worksheet':binding(worksheet),
                     'unchanged_low_alpha_border_declarations':w['source_css']['unchanged_low_alpha_rgba_border_declarations']}
                if kind=='identity': row['identity_fields']=b['identity_fields'];row['literal_candidate_fields_visible']=True
                if kind=='slb' and si==1 and bi==0:
                    row['limitations']=['Proprietary style reference retained; its hidden-fact semantics are unverified. Admission covers literal visible source wording only.']
                if row['unchanged_low_alpha_border_declarations']:
                    row['limitations']=['Known low-alpha border opacity defect remains. Actual row/column/label mapping is unambiguous in this inspected view; no general border-fidelity claim.']
                src['blocks'].append(row)
            result['sources'].append(src)
        rows=[b for s in result['sources'] for b in s['blocks']]
        assert len(rows)==count
        result['totals']={'requested_blocks':count,'admitted_blocks':count,'excluded_blocks':0,'raster_pages_inspected':sum(b['raster_pages_available'] for b in rows)}
        if kind=='contrast':result['totals'].update(tables=2,neighbors=6)
        out=ROOT/'results'/f'{stem}_admission_root_{version}.json'
        with out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
        print(out.name,digest(out))

if __name__=='__main__':main()

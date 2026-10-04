#!/usr/bin/env python3
"""Serialize root's already completed v18 visual review; not a visual grader."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT.parent.parent / 'external'

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def binding(p):
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': digest(p)}

def main():
    # These are human-readable observations from root's actual twelve-page read.
    notes = {
        (6, 0): 'Single italic left-aligned section heading, complete readable line.',
        (6, 1): 'Single left-aligned uppercase company-and-subsidiaries line.',
        (6, 2): 'Single parenthetical table-unit line; parentheses intact.',
        (6, 3): 'Seven readable lines; consolidation/control and noncontrolling-interest qualifications retained.',
        (7, 0): 'Single centered bold section heading.',
        (7, 1): 'Single centered bold uppercase company-and-subsidiaries line.',
        (7, 2): 'Single centered bold parenthetical table-unit line.',
        (7, 3): 'Seven readable lines; entire consolidation policy paragraph retained.',
        (8, 0): 'Two readable lines; equipment list and parenthetical scale retained.',
        (9, 0): 'Seven readable serif lines; quarter-lag qualification and year remain legible.',
        (9, 1): 'Single readable serif line; investee attribution and reporting lag retained.',
        (10, 0): 'Two readable lines; italic statement title retained within the sentence.',
    }
    indexp = EXT / 'source_render_dependencies_v18/attempt01/review_index.json'
    index = json.loads(indexp.read_text())
    private = EXT / 'source_render_review_v18/root_actual_dependency_review'
    private.mkdir(parents=True, exist_ok=True)
    observations, sources = [], []
    for s in index['sources']:
        si = s['source_index']
        if si not in range(6, 11):
            continue
        sr = {k:s[k] for k in ('source_index','source_path','source_sha256')}
        sr['original_source_index'] = s.get('original_source_index', si)
        sr['blocks'] = []
        for b in s['blocks']:
            bi = b['block_index']
            wp = EXT / f'source_render_review_v18/dependencies_binding_attempt01/source_{si:02d}/block_{bi:02d}/worksheet.json'
            w = json.loads(wp.read_text())
            assert (si, bi) in notes and all(w['checks'].values())
            assert b['status'] == 'rendered_pending_full_visual_and_css_review'
            assert w['anchor'] == b['anchor'] and len(b['pngs']) == 1
            assert w['public_checks']['pdf_source_text_check']['matches']
            assert not w['source_css']['hiding_nodes']
            assert not w['source_css']['risk_declarations']
            assert not w['source_css']['unchanged_low_alpha_rgba_border_declarations']
            assert not w['source_rows'] and not w['source_numeric_occurrences']
            deps = w['public_checks']['source_dependency_counts']
            assert all(deps[k] == 0 for k in ('base_elements','head_style_elements','out_of_head_style_elements','script_elements','stylesheet_links'))
            artifacts = []
            for name, a in list(b['artifacts'].items()) + [('raster/'+Path(p['path']).name,p) for p in b['pngs']]:
                p = Path(a['path'])
                assert digest(p) == a['sha256'] and p.stat().st_size == a['bytes']
                artifacts.append({'filename':name,'bytes':a['bytes'],'sha256':a['sha256']})
            observations.append({'source_index':si,'block_index':bi,'block_id':b['block_id'],
                'actual_raster_pages_viewed':[p['sha256'] for p in b['pngs']],
                'visual_readback':notes[(si,bi)], 'source_text_checked':w['source_text'],
                'readable':True,'clipping_overlap':False,'material_grouping_ambiguity':False,
                'original_css_and_ledger_reviewed':True,'source_pdf_whitespace_insensitive_equal':True})
            sr['blocks'].append({'block_id':b['block_id'],'role':b['role'],'dom_path':b['dom_path'],
                'anchor':b['anchor'],'render_status':b['status'],'decision':'admitted_as_qualified_inspection_view',
                'raster_pages_available':1,'raster_page_indices_inspected':[0],
                'checks':{'source_identity_and_fragment':'pass','assembly_and_render_input':'pass',
                  'exact_compatibility_ledger':'pass','source_css_dependencies':'pass',
                  'all_pages_visually_inspected':'pass','text_glyphs_and_clipping':'pass',
                  'row_cell_span_and_numeric_mapping':'not_applicable_paragraph_without_numeric_occurrences',
                  'in_block_labels_and_markers':'pass','material_border_grouping_ambiguity':'absent_in_inspected_view'},
                'compatibility_patch_count':w['ledger_audit']['patch_count'],
                'reviewed_artifacts':artifacts,'private_worksheet':binding(wp)})
        sources.append(sr)
    assert len(observations) == 12
    op = private / 'visual_observations.json'
    with op.open('x') as f:
        json.dump(observations, f, indent=2); f.write('\n')
    inputs = ['data/reader_binding_v18/source_render_dependencies_protocol_v18.json',
              'data/reader_binding_v18/source_render_review_plan_v18.json',
              'results/source_render_dependencies_execution_v18.json',
              'results/source_render_dependencies_binding_review_v18.json',
              'docs/source_render_admission_contract_v17.txt',
              'docs/source_render_rgba_compatibility_contract_v17_2.txt',
              'results/source_render_rgba_compatibility_visual_qa_v17_2.json']
    result = {'schema_version':'root_qualified_source_view_review_v18',
              'reviewer':'root, model-role actual source and raster inspection',
              'reviewed_at_utc':datetime.now(timezone.utc).isoformat(),
              'input_bindings':{p:digest(ROOT/p) for p in inputs},
              'private_review_index':binding(indexp),'private_actual_observations':binding(op),
              'scope':{'qualified_inspection_only':True,'identity_cards_constructed':False,
                'browser_fidelity_claimed':False,'border_opacity_fidelity_claimed':False,
                'semantic_attachment_verified':False,'complete_evidence_packs':0,
                'questions_authored':0,'model_calls':0},
              'totals':{'requested_blocks':12,'admitted_blocks':12,'excluded_blocks':0,
                        'raster_pages_inspected':12,'compatibility_patches':2},'sources':sources,
              'limitations':['Original whole-page geometry and font identity are not certified.',
                'Known authored low-alpha border failure remains; no such declaration occurs in these twelve blocks.',
                'Readable source views do not establish semantic attachment or answerability.']}
    out = ROOT/'results/source_render_dependencies_admission_root_v18.json'
    with out.open('x') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print(out.name, digest(out))

if __name__ == '__main__':
    main()

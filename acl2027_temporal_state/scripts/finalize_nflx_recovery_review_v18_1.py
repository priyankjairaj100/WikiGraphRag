#!/usr/bin/env python3
"""Bind an already-inspected single recovery page to its source; no renderer."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path('/dev/shm/wikigraph_v15/external/source_render_review_v18')
BINDINGS = {
    'results/source_render_nflx_dependency_recovery_binding_review_v18_1.json': '4a728ba0e32de9a0e539c57c487160aa5377f891742180ee044b5e629c2dc524',
    'results/source_render_nflx_recovery_protocol_review_v18_1.json': 'd9921af04e991b9364aa1b7efd3a9cdabc7025a245b85222e6f766d6dbd935a2',
    'results/source_render_nflx_recovery_freeze_readback_v18_1.json': 'a0ca3902b276962333a2803e44622cba4126ac1ca455a1af5bf204cfd71b3e62',
    'data/reader_binding_v18/source_render_nflx_dependency_recovery_protocol_v18_1.json': 'a05eeb058d35f7025f63f0ea2477ac42d7d9d9a17ef39d504956a2715986988b',
    'data/reader_binding_v18/source_render_review_plan_v18.json': 'f6d2bce90d8e68244d886a36c1d343ba9ba993cee070e2ea1ecb0f4edb2ccd5a',
    'results/source_view_availability_summary_v18.json': '7fac96825e0836130f862d3e083703f2d9b2a9729a4475e802e930a05c6e057c',
}


def record(path):
    p = Path(path)
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}


def squash(text):
    return ''.join(c for c in text if not c.isspace())


def main():
    out = ROOT / 'results/source_render_nflx_dependency_recovery_admission_v18_1.json'
    private = EXTERNAL / 'visual_00_05/nflx_recovery_literal_comparison.json'
    assert not out.exists() and not private.exists()
    for path, sha in BINDINGS.items():
        assert record(ROOT / path)['sha256'] == sha
    review = json.loads((ROOT / 'results/source_render_nflx_dependency_recovery_binding_review_v18_1.json').read_text())
    assert len(review['sources']) == len(review['sources'][0]['blocks']) == 1
    source = review['sources'][0]
    block = source['blocks'][0]
    assert source['original_source_index'] == 3 and block['block_id'] == 'paragraph_746'
    for rec in [review['protocol'], review['execution'], review['review_index'], block['private_worksheet'],
                *block['artifact_bindings'].values(), *block['raster_bindings']]:
        assert record(rec['path']) == {k: rec[k] for k in ('path', 'bytes', 'sha256')}
    w = json.loads(Path(block['private_worksheet']['path']).read_text())
    obs_path = EXTERNAL / 'visual_00_05/nflx_recovery_observation.json'
    obs = json.loads(obs_path.read_text())
    assert obs['raster_pages_actually_inspected'] == [0] and len(block['raster_bindings']) == 1
    assert obs['source_css_inspected'] and obs['readable_unclipped'] and not obs['material_mapping_or_grouping_ambiguity']
    assert block['rendered_successfully'] and all(block['checks'].values())
    assert block['compatibility_counts']['patches'] == 0 and not w['source_css']['hiding_nodes']
    assert squash(obs['literal_readback']) == squash(w['source_text']) == squash(''.join(w['pdf_text_by_page']))
    details = {'manual_observation': obs, 'source_text': w['source_text'], 'pdf_text_by_page': w['pdf_text_by_page'],
               'exact_manual_source_pdf_match_after_unicode_whitespace_removal_only': True,
               'source_css_worksheet': block['private_worksheet']}
    pubblock = {k: block[k] for k in ('block_id', 'anchor', 'dom_path', 'artifact_bindings', 'raster_bindings', 'source_counts', 'source_dependency_counts', 'css_risk_categories', 'compatibility_counts')}
    pubblock.update(kind='paragraph', decision='admitted_as_qualified_inspection_view', reason_codes=[],
                    render_status=block['render_status_preserved'], raster_pages_available=1, raster_page_indices_inspected=[0],
                    checks={k: 'pass' for k in ('source_identity_and_fragment', 'assembly_ledger_nonpatch_bytes_and_render_input',
                                               'source_css_dependencies_examined_with_retained_limitations', 'all_pages_visually_inspected',
                                               'text_glyphs_and_clipping', 'material_grouping_ambiguity_absent',
                                               'complete_manual_source_pdf_literal_match_after_whitespace_only')})
    receipt = {'schema_version': 'single_caption_recovery_admission_v18_1', 'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
               'reviewer': 'table_views_v16', 'input_bindings': BINDINGS | {str(Path(__file__).relative_to(ROOT)): record(__file__)['sha256']},
               'execution': review['execution'], 'review_index': review['review_index'],
               'sources': [{'source_index': 3, 'render_source_index': 0, 'source_path': source['source_path'],
                            'source_sha256': source['source_sha256'], 'blocks': [pubblock]}],
               'totals': {'requested_blocks': 1, 'admitted_as_qualified_views': 1, 'excluded': 0, 'raster_pages_actually_inspected': 1},
               'scope': 'Qualified literal caption inspection only; no verified attachment to candidate numeric tables, complete evidence, financial truth or QA claim.',
               'original_v18_dependency_attempt_preserved': {'requested': 40, 'qualified': 39, 'excluded': 1},
               'unverified_inventory_candidate_bundles': ['p03t37', 'p03t38'],
               'semantic_dependency_attachment_verified': False, 'identity_cards_constructed': 0, 'complete_evidence_packs_admitted': 0,
               'questions_authored': 0, 'references_authored': 0, 'new_render_calls': 0, 'model_calls': 0,
               'private_manual_observation': record(obs_path),
               'limitations': ['Full browser/CSS/font fidelity is not certified.',
                               'Known low-alpha border fidelity failure is retained; this caption has no such border.',
                               'Exact ancestor opening tags are retained; surrounding sibling/table context is omitted.',
                               'The original failed attempt is immutable; this is a separately reviewed recovery.',
                               'The model-role reviewer is not an independent human annotator.']}
    with private.open('x') as handle:
        json.dump(details, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write('\n')
    receipt['private_literal_comparison'] = record(private)
    with out.open('x') as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True); handle.write('\n')
    print(json.dumps({'receipt': record(out), 'totals': receipt['totals']}))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Assemble the completed, private manual identity review; never render or infer it."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path('/dev/shm/wikigraph_v15/external')
REVIEW = EXTERNAL / 'identity_source_render_review_v17_2'
INPUTS = {
    'data/reader_binding_v17/source_render_identity_protocol_v17_2.json': '87f620b00ab56232ff4d72e812c9b09e0d04ea4e3a4091536bc24d29a2924f4f',
    'results/source_render_identity_execution_v17_2.json': '8a4cdfec31e8cc6ca1bef7756fc83fb0318922b9f9450da7fba99aeace2e7207',
    'results/source_render_identity_binding_review_v17_2.json': 'ae1f2549af1863805c3ce2d57e521b5d648a51d5cb4ffe0bada96a98f886b9bc',
    'data/reader_binding_v17/identity_render_review_plan_v17_2.json': 'c1362b25f25cb0fed393b45b99a6b4167cb681905ffa53fe078dcf3a9d23d9df',
    'results/source_render_rgba_compatibility_visual_qa_v17_2.json': '800371d7b476e9453ef99e130be5ae79c33f9c6c1f1c07ccc9cbbc70c6792612',
}


def need(value, reason):
    if not value:
        raise ValueError(reason)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def record(path):
    path = Path(path)
    return {'filename': str(path), 'bytes': path.stat().st_size, 'sha256': digest(path)}


def write_new_or_match(path, value):
    if path.exists():
        need(read(path) == value, 'existing_private_evidence_differs')
        return
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write('\n')


def main():
    target = ROOT / 'results/identity_source_render_admission_00_05_v17_2.json'
    private = REVIEW / 'final_00_05'
    need(not target.exists(), 'public_receipt_exists')
    need(not REVIEW.is_symlink() and not private.is_symlink() and not private.resolve().is_relative_to(ROOT.parent), 'private_boundary_invalid')
    for name, expected in INPUTS.items():
        need(digest(ROOT / name) == expected, 'input_hash_mismatch')
    protocol = read(ROOT / 'data/reader_binding_v17/source_render_identity_protocol_v17_2.json')
    result = read(ROOT / 'results/source_render_identity_execution_v17_2.json')
    mechanical = read(ROOT / 'results/source_render_identity_binding_review_v17_2.json')
    index_path = EXTERNAL / 'source_render_identity_v17_2/attempt01/review_index.json'
    need(digest(index_path) == 'f87d24dabbf969ec05e6d7df408f81854d88af5a8751f4ac04aaab30d0e7caf3', 'index_hash_mismatch')
    index = read(index_path)
    observations_path = REVIEW / 'visual_00_05/observations.json'
    comparisons_path = REVIEW / 'visual_00_05/literal_readback_comparisons.json'
    need(digest(comparisons_path) == 'df6ca0e20d3d0b683a54489256114bfbcc90087c0aa51e0eb3948d8a7a1fad82', 'manual_comparisons_changed')
    observations = read(observations_path)
    need(observations['available_pages_actually_viewed'] == 17, 'manual_page_population_changed')
    comparisons = read(comparisons_path)
    lookup = {(b['source_index'], b['block_index']): b for b in comparisons['blocks']}
    need(len(lookup) == 17, 'manual_readback_population_changed')
    private.mkdir(parents=True, exist_ok=True)
    prior_files = {p.name: digest(p) for p in private.iterdir() if p.is_file()}
    sources = []
    admitted = excluded = pages = 0
    compact = lambda text: re.sub(r'\s+', '', text)
    for si in range(6):
        ps, rs, ms, ix = protocol['sources'][si], result['sources'][si], mechanical['sources'][si], index['sources'][si]
        need(all(s['source_sha256'] == ps['source_sha256'] for s in (rs, ms, ix)), 'source_identity_mismatch')
        need(len(ps['blocks']) == len(rs['blocks']) == len(ms['blocks']) == len(ix['blocks']) == 3, 'source_block_denominator_mismatch')
        need(all(ms['dependency_counts'][k] == 0 for k in ('base_elements', 'head_style_elements', 'out_of_head_style_elements', 'script_elements', 'stylesheet_links')), 'unreviewed_stylesheet_dependency')
        source = {'source_index': si, 'source_path': ps['source_path'], 'source_sha256': ps['source_sha256'], 'requested_blocks': 3, 'blocks': []}
        for bi, (pb, rb, mb, ib) in enumerate(zip(ps['blocks'], rs['blocks'], ms['blocks'], ix['blocks'])):
            need(all(all(b[k] == pb[k] for k in ('block_id', 'dom_path', 'anchor')) for b in (rb, mb, ib)), 'block_identity_mismatch')
            need(mb['mechanical_binding_status'] == 'available_source_and_compatibility_artifacts_verified', 'mechanical_check_incomplete')
            required = ('available_artifact_hashes_and_receipt', 'final_render_input_only_utf8_metadata_added', 'ledger_literal_style_positions_and_nonpatch_replay', 'original_fragment_ancestor_head_style_assembly_bytes', 'original_source_anchor')
            need(all(mb['checks'].get(k) is True for k in required), 'source_transport_check_failed')
            need(mb['css_risk_categories'] == [] and mb['source_counts']['hiding_nodes'] == mb['source_counts']['low_alpha_rgba_border_declarations'] == mb['compatibility_counts']['patches'] == 0, 'unresolved_source_CSS_risk')
            worksheet_path = Path(mb['private_worksheet']['path'])
            need(digest(worksheet_path) == mb['private_worksheet']['sha256'], 'worksheet_changed')
            worksheet = read(worksheet_path)
            need(worksheet['source_css']['risk_declarations'] == worksheet['source_css']['hiding_nodes'] == [], 'private_CSS_risk_mismatch')
            good = rb['status'] == 'rendered_pending_full_visual_and_css_review'
            need(good == bool(ib['pngs']), 'render_raster_availability_mismatch')
            checks = {'source_identity_and_fragment': 'pass', 'assembly_ledger_nonpatch_bytes_and_render_input': 'pass', 'source_css_dependencies': 'pass', 'all_available_pages_visually_inspected': 'pass' if good else 'fail', 'text_glyphs_and_clipping': 'pass' if good else 'unknown', 'literal_candidate_field_source_text_visible': 'pass' if good else 'unknown', 'material_border_or_grouping_ambiguity_absent': 'pass' if good else 'unknown'}
            detail = {'source_index': si, 'block_index': bi, 'block_id': pb['block_id'], 'dom_path': pb['dom_path'], 'anchor': pb['anchor'], 'identity_fields': pb['identity_fields'], 'mechanical_worksheet': mb['private_worksheet'], 'mechanical_source_text': worksheet['source_text'], 'all_source_CSS_risks': worksheet['source_css']['risk_declarations'], 'original_render_status_preserved': rb['status'], 'checks': checks, 'legal_entity_equivalence_or_general_scope_inferred': False}
            if good:
                comparison = lookup[si, bi]
                need(compact(comparison['manual_raster_literal_readback']) == compact(worksheet['source_text']), 'manual_source_readback_mismatch')
                need(mb['pdf_source_text_check']['matches'] is True and mb['checks']['rendered_page_denominator'] is True and len(ib['pngs']) == 1, 'PDF_or_page_check_failed')
                for artifact in [*ib['artifacts'].values(), *ib['pngs']]:
                    need(digest(artifact['path']) == artifact['sha256'] and Path(artifact['path']).stat().st_size == artifact['bytes'], 'artifact_changed')
                detail['actual_visual_comparison'] = comparison
                detail['actual_visual_finding'] = 'Readable opaque single literal line, no clipping/overlap or material grouping/border ambiguity; whole original raster page viewed.'
                decision, reasons, inspected = 'admitted_as_qualified_inspection_view', [], [0]
                admitted += 1
                pages += 1
            else:
                need((si, bi, pb['block_id']) == (4, 0, 'identity_142881') and not ib['pngs'], 'unexpected_failure_population')
                detail['actual_visual_comparison'] = None
                detail['actual_visual_finding'] = 'No raster was produced after original conversion telemetry failure. Retained PDF/source text does not replace the required successful raster review.'
                decision, reasons, inspected = 'excluded', ['conversion_process_telemetry_failure', 'required_identity_raster_unavailable'], []
                excluded += 1
            detail['decision'] = decision
            evidence_path = private / f'source_{si:02d}.block_{bi:02d}.{pb["block_id"]}.json'
            write_new_or_match(evidence_path, detail)
            artifacts = [{'filename': name, 'bytes': item['bytes'], 'sha256': item['sha256']} for name, item in ib['artifacts'].items()]
            artifacts += [{'filename': 'raster/' + Path(item['path']).name, 'bytes': item['bytes'], 'sha256': item['sha256']} for item in ib['pngs']]
            source['blocks'].append({'block_id': pb['block_id'], 'kind': 'paragraph', 'identity_fields': pb['identity_fields'], 'dom_path': pb['dom_path'], 'anchor': pb['anchor'], 'render_status': rb['status'], 'decision': decision, 'reason_codes': reasons, 'checks': checks, 'raster_pages_available': len(ib['pngs']), 'raster_page_indices_inspected': inspected, 'reviewed_artifacts': artifacts, 'private_review_evidence': record(evidence_path)})
        sources.append(source)
    need((admitted, excluded, pages) == (17, 1, 17), 'review_totals_mismatch')
    need(all(digest(private / name) == expected for name, expected in prior_files.items()), 'prior_private_file_modified')
    receipt = {'schema_version': 'identity_source_render_admission_v17_2', 'reviewer': 'table_views_v16', 'reviewed_at_utc': datetime.now(timezone.utc).isoformat(), 'assigned_source_indices': list(range(6)), 'input_bindings': [{'filename': p, 'sha256': v} for p, v in INPUTS.items()] + [record(index_path), record(observations_path), record(comparisons_path), {'filename': str(Path(__file__).relative_to(ROOT)), 'sha256': digest(__file__)}], 'scope': {'qualified_inspection_views_only': True, 'literal_candidate_field_visibility_checked': True, 'financial_truth_claimed': False, 'browser_equivalence_claimed': False, 'legal_entity_equivalence_claimed': False, 'broader_period_unit_population_scope_inferred': False, 'complete_evidence_verified': False, 'question_or_reference_admission': False, 'questions_authored': 0, 'model_calls': 0}, 'sources': sources, 'totals': {'sources': 6, 'requested_identity_blocks': 18, 'admitted_blocks': 17, 'excluded_blocks': 1, 'raster_pages_inspected': 17, 'literal_source_PDF_raster_readback_comparisons': 17, 'failed_blocks_retried_or_replaced': 0}, 'receipt_construction_history': {'initial_inline_assembly_error': 'KeyError: filename while serializing a PNG record; PNG records provide path instead.', 'public_receipt_written_before_correction': False, 'private_comparison_files_preserved_from_initial_attempt': prior_files, 'correction': 'Derive PNG filename from its recorded path; validate existing private comparison files by exact JSON equality and unchanged byte digest.', 'source_or_rendered_artifacts_modified': False, 'new_native_calls': 0}, 'limitations': ['Font identity/full page geometry and browser/CSS fidelity remain unverified.', 'The authored border-opacity failure remains recorded; these18 source candidates have no low-alpha border declarations and17 inspected rasters show no material grouping ambiguity.', 'The original source04 DocumentType conversion failure remains excluded even though a PDF was retained; no new rasterization or substitute witness was used.', 'Visible literal issuer/form/period wording is source-bound inspection evidence only; no broader question scope or legal-identity inference.']}
    write_new_or_match(target, receipt)
    print(json.dumps({'receipt': str(target.relative_to(ROOT)), 'sha256': digest(target), 'totals': receipt['totals']}))


if __name__ == '__main__':
    main()

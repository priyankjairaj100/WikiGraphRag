#!/usr/bin/env python3
"""Bind already-authored v18 visual decisions; never render or invent reviews.

Fixed source-bearing observation/worksheet inputs remain outside the repository.
Run once after the assigned actual page and source/CSS review. This script checks
coverage, exact bindings and selected literal readbacks; it does not perform or
certify visual inspection, semantic attachment, or full browser/CSS fidelity.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path('/dev/shm/wikigraph_v15/external/source_render_review_v18')
PLAN = 'data/reader_binding_v18/source_render_review_plan_v18.json'
PLAN_SHA = 'f6d2bce90d8e68244d886a36c1d343ba9ba993cee070e2ea1ecb0f4edb2ccd5a'
JOBS = [
    ('dependencies', list(range(6)), 28, 27,
     'source_render_dependencies_protocol_v18.json',
     'source_render_dependencies_binding_review_v18.json',
     'd33173ad7ec75e74b70e9ca6587ead6e4515bc29a70f6ec50635047f759d3da9',
     'dependencies_observations.json',
     'source_render_dependencies_admission_00_05_v18.json'),
    ('nvda_identity_recovery', [4], 1, 1,
     'source_render_nvda_identity_recovery_protocol_v18.json',
     'source_render_nvda_identity_recovery_binding_review_v18.json',
     '1ba19b98b4b44eb808d04b8d511c400f4fbd0cc76e0f33e9f2d064cb605e2986',
     'nvda_recovery_observation.json',
     'source_render_nvda_identity_recovery_admission_v18.json'),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path):
    p = Path(path)
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)}


def read_bound(path, expected=None):
    p = Path(path)
    if expected is not None:
        assert sha(p) == expected, f'Binding mismatch: {p}'
    return json.loads(p.read_text())


def verify_record(rec):
    assert record(rec['path']) == {k: rec[k] for k in ('path', 'bytes', 'sha256')}


def squash(value):
    return ''.join(c for c in value if not c.isspace())


def main():
    plan = read_bound(ROOT / PLAN, PLAN_SHA)
    for path, digest in plan['input_bindings'].items():
        assert sha(ROOT / path) == digest
    assert not ROOT.is_relative_to(EXTERNAL) and not EXTERNAL.is_relative_to(ROOT)
    pending = []
    for mode, indices, expected_blocks, expected_pages, protocol_name, review_name, review_sha, obs_name, output_name in JOBS:
        proto_path = ROOT / 'data/reader_binding_v18' / protocol_name
        proto = read_bound(proto_path, plan['input_bindings'][str(proto_path.relative_to(ROOT))])
        review_path = ROOT / 'results' / review_name
        review = read_bound(review_path, review_sha)
        for key in ('protocol', 'execution', 'review_index', 'checker'):
            verify_record(review[key])
        obs_path = EXTERNAL / 'visual_00_05' / obs_name
        notes = read_bound(obs_path)['observations']
        assert set(map(int, notes)) == set(indices)
        sources, comparisons = [], []
        total = admitted = pages = 0
        for ms in review['sources']:
            original = ms['original_source_index']
            if original not in indices:
                continue
            ps = next(s for s in proto['sources'] if s['original_source_index'] == original)
            assert ms['source_sha256'] == ps['source_sha256']
            assert len(ms['blocks']) == len(ps['blocks']) == len(notes[str(original)])
            blocks = []
            for mb, pb in zip(ms['blocks'], ps['blocks']):
                bi = mb['block_index']
                note = notes[str(original)][str(bi)]
                assert mb['block_id'] == pb['block_id'] and mb['anchor'] == pb['anchor'] and mb['dom_path'] == pb['dom_path']
                assert all(mb['checks'].values()) and mb['pdf_source_text_check']['matches']
                assert mb['mechanical_binding_status'] == 'available_source_and_compatibility_artifacts_verified'
                verify_record(mb['private_worksheet'])
                w = read_bound(mb['private_worksheet']['path'])
                for rec in list(mb['artifact_bindings'].values()) + mb['raster_bindings']:
                    verify_record(rec)
                available = len(mb['raster_bindings'])
                successful = mb['rendered_successfully']
                assert note['pages'] == (list(range(available)) if successful else [])
                assert (successful and available == 1) or (not successful and available == 0)
                assert note['source_css_inspected'] and not w['source_css']['hiding_nodes']
                assert mb['compatibility_counts']['patches'] == 0
                assert not w['source_css']['unchanged_low_alpha_rgba_border_declarations']
                tokens = note.get('readback_tokens', [note['literal_readback']] if 'literal_readback' in note else [])
                text, pdf = squash(w['source_text']), squash(''.join(w['pdf_text_by_page']))
                assert text == pdf
                assert all(squash(token) in text for token in tokens)
                if 'literal_readback' in note:
                    assert squash(note['literal_readback']) == text
                if successful:
                    assert note['readable_unclipped'] and not note['material_mapping_or_grouping_ambiguity']
                comparisons.append({'original_source_index': original, 'block_id': pb['block_id'], 'observation': note,
                                    'source_text': w['source_text'], 'pdf_text_by_page': w['pdf_text_by_page'],
                                    'exact_source_pdf_after_whitespace_only': True,
                                    'selected_visual_token_count': len(tokens), 'selected_tokens_match_source': True,
                                    'token_list_is_complete_transcription': 'literal_readback' in note,
                                    'worksheet': mb['private_worksheet']})
                checks = {name: 'pass' for name in ('source_identity_and_fragment', 'assembly_ledger_nonpatch_bytes_and_render_input',
                          'source_css_dependencies_examined_with_retained_limitations', 'source_pdf_exact_after_whitespace_only')}
                checks.update({name: 'pass' if successful else 'unavailable' for name in
                               ('all_available_pages_visually_inspected', 'text_glyphs_and_clipping', 'material_grouping_ambiguity_absent')})
                block_kind = pb['kind'] if mode == 'dependencies' else 'identity_dependency_candidate'
                block = {'block_id': pb['block_id'], 'block_index': bi, 'kind': block_kind, 'dom_path': pb['dom_path'],
                         'anchor': pb['anchor'], 'render_status': mb['render_status_preserved'],
                         'decision': 'admitted_as_qualified_inspection_view' if successful else 'excluded',
                         'reason_codes': [] if successful else ['original_telemetry_failure_no_raster'],
                         'checks': checks, 'raster_pages_available': available, 'raster_page_indices_inspected': note['pages'],
                         'artifact_bindings': mb['artifact_bindings'], 'raster_bindings': mb['raster_bindings'],
                         'source_counts': mb['source_counts'], 'css_risk_categories': mb['css_risk_categories'],
                         'mechanical_worksheet': mb['private_worksheet'],
                         'source_dependency_counts': mb['source_dependency_counts'],
                         'compatibility_counts': mb['compatibility_counts']}
                if 'source_cell_structure' in note:
                    block['reviewed_source_cell_structure'] = note['source_cell_structure']
                if 'roles' in pb:
                    block['unverified_inventory_roles'] = pb['roles']
                    block['unverified_inventory_candidate_bundles'] = pb['candidate_bundles']
                if 'identity_fields' in pb:
                    block['literal_candidate_fields'] = pb['identity_fields']
                blocks.append(block)
                total += 1
                admitted += int(successful)
                pages += len(note['pages'])
            sources.append({'source_index': original, 'render_source_index': ms['source_index'],
                            'source_path': ps['source_path'], 'source_sha256': ps['source_sha256'], 'blocks': blocks})
        assert total == expected_blocks and admitted == pages == expected_pages
        private_path = EXTERNAL / 'visual_00_05' / (mode + '_literal_comparisons.json')
        public_path = ROOT / 'results' / output_name
        assert not private_path.exists() and not public_path.exists()
        private = {'schema_version': 'source_view_readback_evidence_v18', 'mode': mode, 'blocks': comparisons}
        public = {'schema_version': 'source_render_admission_v18', 'mode': mode, 'reviewer': 'table_views_v16',
                  'reviewed_at_utc': datetime.now(timezone.utc).isoformat(), 'assigned_source_indices': indices,
                  'scope': 'Qualified views of fixed source blocks only; no dependency semantic attachment, identity-card admission, financial truth or QA evaluation.',
                  'input_bindings': {PLAN: PLAN_SHA, str(review_path.relative_to(ROOT)): review_sha,
                                     str(proto_path.relative_to(ROOT)): sha(proto_path),
                                     str(Path(__file__).relative_to(ROOT)): sha(__file__)},
                  'execution': review['execution'], 'review_index': review['review_index'],
                  'private_manual_observations': record(obs_path),
                  'sources': sources, 'totals': {'requested_blocks': total, 'admitted': admitted, 'excluded': total - admitted,
                                               'raster_pages_actually_inspected': pages, 'rgba_compatibility_patches': 0},
                  'limitations': plan['retained_limitations'] + [
                      'Exact original ancestor openings and head styles retained; sibling content/column-sizing rows outside a selected whole caption row are not imported.',
                      'Selected literal token readbacks supplement actual full-page inspection and complete mechanical source/PDF text equality; they are not full manual transcriptions.',
                      'Definition tables and isolated caption rows are qualified for visible text only; their semantic attachment to numeric tables remains pending.',
                      'Agents review disjoint source subsets; no duplicate annotation, role independence or inter-annotator agreement claim.'],
                  'questions_authored': 0, 'references_authored': 0, 'model_calls': 0, 'new_render_calls': 0,
                  'automatic_retries': 0, 'dependency_semantic_attachment_verified': False,
                  'identity_cards_automatically_admitted': False, 'prior_attempt_outcomes_preserved': True,
                  'receipt_construction_history': [{
                      'attempt': 1, 'status': 'failed_before_any_receipt_write',
                      'script_sha256': '83aa21dbd096f9b73f0339bb95d5814fd99bd770d02261e24dda61157e6bcf70',
                      'exception': "KeyError: 'kind'", 'line': 119,
                      'cause': 'Recovery identity protocol has a role and no kind field; draft assembler assumed dependency metadata shape.',
                      'fix': 'Keep dependency kind; explicitly label recovery as identity_dependency_candidate.',
                      'new_render_or_model_calls': 0, 'native_process_launches': 0}]}
        pending.append((private_path, private, public_path, public))
    for private_path, private, public_path, public in pending:
        with private_path.open('x') as handle:
            json.dump(private, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write('\n')
        public['private_literal_comparisons'] = record(private_path)
        with public_path.open('x') as handle:
            json.dump(public, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write('\n')
        print(json.dumps({'receipt': record(public_path), 'totals': public['totals']}))


if __name__ == '__main__':
    main()

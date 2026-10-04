#!/usr/bin/env python3
"""Aggregate fixed v18 inspection receipts without changing earlier outcomes."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = 'results/source_view_availability_summary_v18.json'
BINDINGS = {
    'results/source_view_availability_summary_v17_2.json': 'a5a9b7f5f73642df8ba47f834c0c9c1e2789139e2d7cca48629148cb006bef60',
    'results/pre_author_bundle_inventory_v17.json': 'b912a697a6ce1635fbd66ab7f7201fa69084f83b1e60a4ba585870d1ef02fe1b',
    'data/reader_binding_v18/source_render_review_plan_v18.json': 'f6d2bce90d8e68244d886a36c1d343ba9ba993cee070e2ea1ecb0f4edb2ccd5a',
    'data/reader_binding_v18/source_render_dependencies_protocol_v18.json': '0af419d76331858e7a469a83ac1028db40dd0ffbd9f19d642d78d89c3e7fcf66',
    'data/reader_binding_v18/source_render_nvda_identity_recovery_protocol_v18.json': '9e19568a240b8066231d48761ba567687572c203cdeda5ec8603f9370b9e679c',
    'results/source_render_population_review_v18.json': '98152a8593b126e07b2f0622bba9d99d01bc132f1f9d10b5cb89b29d92df6250',
    'results/source_render_dependencies_execution_v18.json': '502db820bf8bc575cf371141d42f7e78395dc31ab2ede3c87482d1664e5c453c',
    'results/source_render_nvda_identity_recovery_execution_v18.json': '2bf9ec0e57f52f67d21460a42f05679b08bf1527629c24538e6654facb010fd8',
    'results/source_render_dependencies_binding_review_v18.json': 'd33173ad7ec75e74b70e9ca6587ead6e4515bc29a70f6ec50635047f759d3da9',
    'results/source_render_nvda_identity_recovery_binding_review_v18.json': '1ba19b98b4b44eb808d04b8d511c400f4fbd0cc76e0f33e9f2d064cb605e2986',
    'results/source_render_dependencies_admission_00_05_v18.json': '295efc3fd6551e69d4fb46bb489ac68c75a98895eeb2358e4869f9517924e0ea',
    'results/source_render_dependencies_admission_root_v18.json': 'ba9b437048b960a24830560f2bc26ea1461494349d473c08f9a2547685d1eea1',
    'results/source_render_nvda_identity_recovery_admission_v18.json': '72f1fce162f8fa08fbd7f25a3115075ca674b4d074111a4bcc94f5aec5f4f5d7',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = ROOT / OUTPUT
    assert not output.exists()
    docs = {}
    for path, sha in BINDINGS.items():
        assert digest(ROOT / path) == sha, path
        docs[path] = json.loads((ROOT / path).read_text())
    for doc in docs.values():
        for path, sha in doc.get('input_bindings', {}).items():
            assert digest(ROOT / path) == sha, path
    prior = docs['results/source_view_availability_summary_v17_2.json']
    inventory = docs['results/pre_author_bundle_inventory_v17.json']
    required = {(b['source_index'], b['block_id']): b for b in inventory['additional_dependency_blocks']}
    assert len(required) == len(inventory['additional_dependency_blocks']) == 40
    lineage, seen = [], set()
    for name, assigned in [('source_render_dependencies_admission_00_05_v18.json', set(range(6))),
                           ('source_render_dependencies_admission_root_v18.json', set(range(6, 12)))]:
        path = 'results/' + name
        review = docs[path]
        for source in review['sources']:
            si = source['source_index']
            assert si in assigned
            for block in source['blocks']:
                key = (si, block['block_id'])
                assert key not in seen
                seen.add(key)
                expected = required[key]
                assert source['source_path'] == expected['source_path'] and source['source_sha256'] == expected['source_sha256']
                assert all(block[k] == expected[k] for k in ('anchor', 'dom_path'))
                ok = block['decision'] == 'admitted_as_qualified_inspection_view'
                assert block['decision'] in ('admitted_as_qualified_inspection_view', 'excluded')
                assert block['raster_page_indices_inspected'] == ([0] if ok else [])
                assert block['raster_pages_available'] == int(ok)
                lineage.append({k: expected[k] for k in ('source_index', 'source_path', 'source_sha256', 'block_id', 'kind', 'dom_path', 'anchor')} |
                               {'decision': block['decision'], 'original_render_status_preserved': block['render_status'],
                                'admission_receipt': path, 'raster_pages_actually_inspected': len(block['raster_page_indices_inspected']),
                                'unverified_inventory_roles': expected['roles'],
                                'unverified_inventory_candidate_bundles': expected['candidate_bundles']})
    assert seen == set(required)
    lineage.sort(key=lambda b: (b['source_index'], b['anchor']['byte_start'], b['block_id']))
    decisions = Counter(b['decision'] for b in lineage)
    assert decisions == {'admitted_as_qualified_inspection_view': 39, 'excluded': 1}
    failed = [b for b in lineage if b['decision'] == 'excluded']
    assert [(b['source_index'], b['block_id']) for b in failed] == [(3, 'paragraph_746')]
    blocked = sorted(set(x for b in failed for x in b['unverified_inventory_candidate_bundles']))
    assert blocked == ['p03t37', 'p03t38']
    recovery_path = 'results/source_render_nvda_identity_recovery_admission_v18.json'
    recovery = docs[recovery_path]
    rs = recovery['sources'][0]
    rb = rs['blocks'][0]
    assert len(recovery['sources']) == len(rs['blocks']) == 1
    assert rs['source_index'] == 4 and rb['block_id'] == 'identity_142881'
    assert rb['decision'] == 'admitted_as_qualified_inspection_view' and rb['raster_page_indices_inspected'] == [0]
    identity_lineage = []
    for original in prior['identity_block_lineage']:
        entry = dict(original)
        entry['original_decision'] = original['decision']
        entry['original_receipt'] = original['receipt']
        entry['derived_available_decision'] = original['decision']
        entry['separate_recovery_receipt'] = None
        if (original['source_index'], original['block_id']) == (4, rb['block_id']):
            assert original['decision'] == 'excluded' and original['anchor'] == rb['anchor']
            assert original['source_sha256'] == rs['source_sha256']
            entry['derived_available_decision'] = rb['decision']
            entry['separate_recovery_receipt'] = recovery_path
        identity_lineage.append(entry)
    assert len(identity_lineage) == 34
    assert sum(b['derived_available_decision'] == 'admitted_as_qualified_inspection_view' for b in identity_lineage) == 34
    assert sum(b['separate_recovery_receipt'] is not None for b in identity_lineage) == 1
    bindings = dict(BINDINGS)
    bindings[str(Path(__file__).relative_to(ROOT))] = digest(Path(__file__))
    result = {
        'schema_version': 'source_view_availability_summary_v18', 'aggregated_at_utc': datetime.now(timezone.utc).isoformat(),
        'input_bindings': bindings,
        'scope': 'Qualified inspection availability only. Original attempts retain their exact outcomes; no semantic evidence or identity cards are admitted.',
        'preserved_v17_original_attempt': prior['original_attempt'],
        'preserved_v17_telemetry_recovery': prior['separate_telemetry_recovery'],
        'preserved_v17_contrast_recovery': prior['separate_contrast_recovery'],
        'derived_original_population_availability_unchanged': prior['derived_original_population_availability'],
        'preserved_v17_identity_attempt': prior['identity_candidate_population'],
        'preserved_separate_slb_literal_cover': prior['separate_slb_literal_cover'],
        'v18_dependencies_attempt': {'requested_blocks': 40, 'requested_sources': 11, 'original_source11_additions': 0,
                                    'rendered': 39, 'admitted_as_qualified_views': 39, 'excluded': 1,
                                    'raster_pages_actually_inspected': 39, 'rgba_compatibility_patches': 2,
                                    'source_kind_counts': dict(Counter(b['kind'] for b in lineage)), 'automatic_retries': 0},
        'v18_separate_nvda_identity_recovery': {'requested_blocks': 1, 'rendered': 1, 'admitted_as_qualified_views': 1,
                                             'raster_pages_actually_inspected': 1, 'original_failed_attempt_preserved': True},
        'derived_identity_candidate_availability': {'requested_blocks': 34, 'available_qualified_views': 34,
                                                  'original_attempt_admitted': 33, 'separate_recovery_admitted': 1,
                                                  'complete_source_identity_cards_constructed': 0,
                                                  'SLB_literal_cover_supplement_remains_separate': True},
        'blocked_bundle_handles_from_unavailable_dependency_view': blocked,
        'blocking_scope': 'These two candidate bundles have a missing qualified caption view. Other bundles are not thereby admitted; all semantic prerequisites remain pending.',
        'source_only_semantic_prerequisites': {'identity_cards_constructed': 0, 'semantic_dependency_attachments_verified': 0,
                                              'unit_period_entity_population_scope_bindings_admitted': 0,
                                              'complete_evidence_packs_admitted': 0, 'answerable_packs_admitted': 0,
                                              'questions_authored': 0, 'reference_answers_authored': 0,
                                              'natural_QA_evaluation_predictions_from_this_stage': 0},
        'dependency_block_lineage': lineage, 'identity_block_lineage': identity_lineage,
        'verification': {'dependency_subsets_disjoint_and_complete_40': True, 'all_dependency_anchors_equal_frozen_inventory': True,
                         'identity_overlay_exact_previously_failed_anchor_only': True, 'old_receipts_modified': False},
        'limitations': prior['limitations'] + docs['data/reader_binding_v18/source_render_review_plan_v18.json']['retained_limitations'] + [
            'Isolated caption rows retain ancestor wrappers but omit sibling rows and their sizing/context. Whole definition tables preserve source cell spans; no dependency-to-numeric-table semantic attachment is inferred.',
            'Original v18 caption telemetry failure and original v17 identity telemetry failure are not relabeled by derived availability.',
            'Availability of literal form and issuer-name candidate views is not legal-identity equivalence or a completed source identity card.'],
    }
    with output.open('x') as handle:
        json.dump(result, handle, indent=2, sort_keys=True); handle.write('\n')
    print(json.dumps({'path': OUTPUT, 'bytes': output.stat().st_size, 'sha256': digest(output),
                      'dependencies': result['v18_dependencies_attempt'], 'identity': result['derived_identity_candidate_availability']}))


if __name__ == '__main__':
    main()

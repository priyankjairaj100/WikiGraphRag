#!/usr/bin/env python3
"""Overlay exactly one separately qualified recovery; preserve prior outcomes."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIOR = 'results/source_view_availability_summary_v18.json'
RECOVERY = 'results/source_render_nflx_dependency_recovery_admission_v18_1.json'
BINDINGS = {
    PRIOR: '7fac96825e0836130f862d3e083703f2d9b2a9729a4475e802e930a05c6e057c',
    RECOVERY: '1def91304d3703c20471c4152e7f974734df6834deccd5976b48cf8295a9aa5b',
    'data/reader_binding_v18/source_render_nflx_dependency_recovery_protocol_v18_1.json': 'a05eeb058d35f7025f63f0ea2477ac42d7d9d9a17ef39d504956a2715986988b',
    'results/source_render_nflx_dependency_recovery_execution_v18_1.json': 'f162b03c6967414753026a613d9ef1720ba933eef54dd443ad5db4929e23f7e5',
    'results/source_render_nflx_dependency_recovery_binding_review_v18_1.json': '4a728ba0e32de9a0e539c57c487160aa5377f891742180ee044b5e629c2dc524',
    'results/source_render_nflx_recovery_protocol_review_v18_1.json': 'd9921af04e991b9364aa1b7efd3a9cdabc7025a245b85222e6f766d6dbd935a2',
    'results/source_render_nflx_recovery_freeze_readback_v18_1.json': 'a0ca3902b276962333a2803e44622cba4126ac1ca455a1af5bf204cfd71b3e62',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    output = ROOT / 'results/source_view_availability_summary_v18_1.json'
    assert not output.exists()
    docs = {}
    for path, digest in BINDINGS.items():
        assert sha(ROOT / path) == digest
        docs[path] = json.loads((ROOT / path).read_text())
    for doc in docs.values():
        for path, digest in doc.get('input_bindings', {}).items():
            assert sha(ROOT / path) == digest
    previous, review = docs[PRIOR], docs[RECOVERY]
    assert previous['v18_dependencies_attempt']['admitted_as_qualified_views'] == 39
    assert previous['v18_dependencies_attempt']['excluded'] == 1
    assert len(review['sources']) == len(review['sources'][0]['blocks']) == 1
    source = review['sources'][0]
    block = source['blocks'][0]
    assert (source['source_index'], block['block_id']) == (3, 'paragraph_746')
    assert block['decision'] == 'admitted_as_qualified_inspection_view' and block['raster_page_indices_inspected'] == [0]
    summary = copy.deepcopy(previous)
    overlay_count = 0
    for entry in summary['dependency_block_lineage']:
        entry['original_decision'] = entry['decision']
        entry['derived_available_decision'] = entry['decision']
        entry['separate_recovery_receipt'] = None
        if (entry['source_index'], entry['block_id']) == (3, 'paragraph_746'):
            assert entry['decision'] == 'excluded'
            assert entry['source_sha256'] == source['source_sha256']
            assert entry['anchor'] == block['anchor'] and entry['dom_path'] == block['dom_path']
            entry['derived_available_decision'] = block['decision']
            entry['separate_recovery_receipt'] = RECOVERY
            entry['separate_recovery_pages_actually_inspected'] = 1
            overlay_count += 1
    assert overlay_count == 1 and len(summary['dependency_block_lineage']) == 40
    assert sum(x['derived_available_decision'] == 'admitted_as_qualified_inspection_view' for x in summary['dependency_block_lineage']) == 40
    summary.update(schema_version='source_view_availability_summary_v18_1', aggregated_at_utc=datetime.now(timezone.utc).isoformat())
    summary['input_bindings'].update(BINDINGS)
    summary['input_bindings'][str(Path(__file__).relative_to(ROOT))] = sha(__file__)
    summary['v18_1_separate_caption_recovery'] = {'requested_blocks': 1, 'rendered': 1, 'admitted_as_qualified_views': 1,
                                               'raster_pages_actually_inspected': 1, 'original39_of40_outcome_preserved': True,
                                               'rgba_compatibility_patches': 0, 'automatic_additional_retries': 0}
    summary['derived_dependency_population_availability'] = {'requested_unique_blocks': 40, 'available_qualified_views': 40,
                                                            'original_attempt_qualified': 39, 'separate_recovery_qualified': 1,
                                                            'remaining_unavailable': 0, 'raster_pages_actually_inspected_across_attempts': 40}
    summary['previously_view_blocked_bundle_handles'] = previous['blocked_bundle_handles_from_unavailable_dependency_view']
    summary['blocked_bundle_handles_from_unavailable_dependency_view'] = []
    summary['blocking_scope'] = 'All40 fixed dependency views are available across attempts. This clears only the missing-view condition for the two previously affected candidates; no semantic attachment, scope binding or complete-evidence admission follows.'
    summary['verification']['separate_caption_overlay_exact_previously_failed_anchor_only'] = True
    assert summary['derived_identity_candidate_availability'] == previous['derived_identity_candidate_availability']
    assert summary['preserved_separate_slb_literal_cover'] == previous['preserved_separate_slb_literal_cover']
    assert summary['source_only_semantic_prerequisites'] == previous['source_only_semantic_prerequisites']
    assert summary['v18_dependencies_attempt'] == previous['v18_dependencies_attempt']
    summary['limitations'].append('The new caption view does not itself resolve either candidate bundle semantic attachment. Identity-card candidate proposals or later card decisions are outside this availability overlay.')
    with output.open('x') as handle:
        json.dump(summary, handle, indent=2, sort_keys=True); handle.write('\n')
    print(json.dumps({'path': str(output.relative_to(ROOT)), 'bytes': output.stat().st_size, 'sha256': sha(output),
                      'dependency_availability': summary['derived_dependency_population_availability']}))


if __name__ == '__main__':
    main()

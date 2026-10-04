#!/usr/bin/env python3
"""Check only the allowed draft-to-freeze metadata delta; no native calls."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFT = 'data/reader_binding_v18/source_render_nflx_dependency_recovery_draft_v18_1.json'
FROZEN = 'data/reader_binding_v18/source_render_nflx_dependency_recovery_protocol_v18_1.json'
ADDITIONS = {
    'results/source_render_nflx_recovery_protocol_review_v18_1.json': 'd9921af04e991b9364aa1b7efd3a9cdabc7025a245b85222e6f766d6dbd935a2',
    'data/reader_binding_v18/source_render_review_plan_v18.json': 'f6d2bce90d8e68244d886a36c1d343ba9ba993cee070e2ea1ecb0f4edb2ccd5a',
}
BINDINGS = {DRAFT: 'c28fca0ab6cf5b39b30409cbd20f54c41629eae61d4474c0d58e123fa3cff9d2',
            FROZEN: 'a05eeb058d35f7025f63f0ea2477ac42d7d9d9a17ef39d504956a2715986988b', **ADDITIONS}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    out = ROOT / 'results/source_render_nflx_recovery_freeze_readback_v18_1.json'
    assert not out.exists()
    for path, sha in BINDINGS.items():
        assert digest(ROOT / path) == sha
    original = json.loads((ROOT / DRAFT).read_text())
    frozen = json.loads((ROOT / FROZEN).read_text())
    assert {k for k in original.keys() | frozen.keys() if original.get(k) != frozen.get(k)} == {'frozen_at_utc', 'input_bindings', 'protocol_status'}
    assert frozen['input_bindings'] == original['input_bindings'] | ADDITIONS
    assert frozen['protocol_status'] == 'frozen_pending_root_exact_hash_execution_approval'
    assert datetime.fromisoformat(frozen['frozen_at_utc']) >= datetime.fromisoformat(original['prepared_at_utc'])
    assert not Path(frozen['planned_external_output']).exists()
    assert not (ROOT / frozen['planned_public_result']).exists()
    receipt = {'schema_version': 'single_caption_recovery_freeze_readback_v18_1',
               'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'allowed_metadata_delta_verified',
               'input_bindings': BINDINGS | {str(Path(__file__).relative_to(ROOT)): digest(__file__)},
               'changed_fields': ['frozen_at_utc', 'input_bindings', 'protocol_status'],
               'source_population_code_runtime_and_limits_unchanged_from_reviewed_draft': True,
               'planned_output_paths_absent_at_readback': True, 'open_blocking_findings': 0,
               'root_exact_hash_execution_approval_still_required': True,
               'execution_authority_granted': False, 'native_launches': 0, 'model_calls': 0}
    with out.open('x') as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True); handle.write('\n')
    print(json.dumps({'path': str(out.relative_to(ROOT)), 'sha256': digest(out), 'frozen_protocol_sha256': BINDINGS[FROZEN]}))


if __name__ == '__main__':
    main()

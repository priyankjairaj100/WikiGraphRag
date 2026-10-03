#!/usr/bin/env python3
"""Compare every v0.8 package entry after final verification and state updates."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
MUTABLE = {'PROJECT_STATE.json', 'TASKS.json', 'README.md', 'paper/README.txt',
           'scripts/verify_project.py', 'results/verification.json'}
REGENERATED = {'data/diagnostics/decoder_results.json', 'data/diagnostics/results.json',
               'results/coupled_v04_predictions.json', 'results/coupled_v04_replay.json'}


def stable(value):
    if isinstance(value, list):
        return [stable(x) for x in value]
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in {'elapsed_seconds', 'created_at_utc'}}
    return value


def main():
    manifest = json.loads((ROOT / 'data/provenance/checkpoint_v08_manifest.json').read_bytes())
    expected = json.loads((ROOT / 'data/provenance/v08_diagnostic_semantics.json').read_bytes())
    rows = []
    for old in manifest['files']:
        path = ROOT / old['path']; assert path.exists(), str(path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        same = actual == old['sha256']
        role = 'protected_v08_file'
        if old['path'] in MUTABLE:
            role = 'updated_project_bookkeeping_or_verification_entrypoint'
        elif old['path'] in REGENERATED:
            role = 'regenerated_diagnostic_output'
            actual_semantics = stable(json.loads(path.read_bytes()))
            expected_semantics = expected[old['path']]
            if old['path'] == 'results/coupled_v04_replay.json':
                prediction_path = 'results/coupled_v04_predictions.json'
                prior_prediction = next(x for x in manifest['files'] if x['path'] == prediction_path)
                assert expected_semantics['prediction_sha256'] == prior_prediction['sha256']
                assert actual_semantics['prediction_sha256'] == hashlib.sha256((ROOT / prediction_path).read_bytes()).hexdigest()
                actual_semantics = {k: v for k, v in actual_semantics.items() if k != 'prediction_sha256'}
                expected_semantics = {k: v for k, v in expected_semantics.items() if k != 'prediction_sha256'}
            assert actual_semantics == expected_semantics, old['path']
        else:
            assert same, 'Unexpected modification: ' + old['path']
        rows.append({'path': old['path'], 'expected_sha256': old['sha256'],
                     'actual_sha256': actual, 'unchanged': same, 'role': role})
    out = {'schema_version': 'input_preservation_v0.9', 'base_checkpoint': 'v0.8',
           'status': 'passed', 'files_checked': len(rows),
           'unchanged_files': sum(x['unchanged'] for x in rows),
           'protected_files': sum(x['role'] == 'protected_v08_file' for x in rows),
           'all_protected_files_unchanged': True,
           'diagnostic_semantics_unchanged': True,
           'ignored_regenerated_fields': ['elapsed_seconds', 'created_at_utc'],
           'rebound_derived_hash': 'coupled_v04_replay.prediction_sha256 checked against the corresponding old/new prediction file; semantic content compared separately',
           'changed_files': [x['path'] for x in rows if not x['unchanged']], 'files': rows}
    (ROOT / 'results/input_preservation_v09.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({k: v for k, v in out.items() if k != 'files'}))


if __name__ == '__main__':
    main()

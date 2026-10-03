"""Offline v0.12 provenance checks; does not certify source meaning."""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def read(relative):
    return json.loads((ROOT / relative).read_bytes())


def verify(external_root=None):
    protocol = read('data/access_pilot_v12/protocol.json')
    authorization = read('data/access_pilot_v12/execution_authorization.json')
    capture = read('results/access_pilot_v12.json')
    admission = read('results/admission_summary_v12.json')
    audit = read('results/access_capture_audit_v12.json')
    for path, expected in authorization['files'].items():
        assert digest(ROOT / path) == expected, path
    for path, expected in audit['inputs'].items():
        assert digest(ROOT / path) == expected, path
    assert capture['status'] == 'complete'
    assert capture['protocol_sha256'] == digest(ROOT / 'data/access_pilot_v12/protocol.json')
    assert capture['frozen_inputs_sha256'] == digest(ROOT / 'data/access_pilot_v12/frozen_inputs.json')
    assert datetime.fromisoformat(authorization['created_at_utc']) < datetime.fromisoformat(capture['started_at'])
    expected_sources = protocol['sources']
    records = capture['records']
    assert len(records) == len(expected_sources) == 8
    previous_end = capture['started_at']
    for index, (record, source) in enumerate(zip(records, expected_sources)):
        assert record['source_id'] == source['source_id']
        assert record['requested_url'] == source['url']
        assert record['source_index'] == index and record['attempt_number'] == 1
        assert record['started_at'] >= previous_end
        assert record['finished_at'] >= record['started_at']
        previous_end = record['finished_at']
        assert record['raw_bytes'] <= 8 * 1024 * 1024
        # Preserve capture-time pending decisions; later reviews are separate.
        assert not record['complete_source_verified']
        assert not record['benchmark_admitted']
    for field in ('transport_success', 'http_success', 'mechanical_parse_success', 'provisional_usability'):
        assert capture['summary'][field] == sum(r[field] for r in records)
    assert capture['summary']['retries'] == 0
    assert audit['all_checks_passed'] and all(x['passed'] for x in audit['checks'])
    for row in admission['review_artifacts']:
        assert digest(ROOT / row['path']) == row['sha256'], row['path']
    decisions = admission['histories']
    counts = admission['counts']
    assert {x['history_id'] for x in decisions} == {x['history_id'] for x in records}
    assert counts['controlled_core_development_controls'] == sum(x['controlled_core_admitted'] for x in decisions)
    assert counts['nondirect_propagation_cases'] == sum(x['nondirect_propagation_case'] for x in decisions)
    assert counts['benchmark_admitted_histories'] == sum(x['benchmark_admitted'] for x in decisions)
    assert counts['complete_intended_representations'] == sum(x['complete_representations'] for x in decisions)
    ledger = read('paper/claim_ledger_v12.json')
    assert digest(ROOT / ledger['draft']) == ledger['draft_sha256']
    for claim in ledger['claims']:
        for artifact in claim['artifacts']:
            assert digest(ROOT / artifact['path']) == artifact['sha256'], artifact['path']
    previous = read('data/provenance/parent_manifest_v11.json')
    allowed_changes = {'README.md', 'PROJECT_STATE.json', 'TASKS.json', 'paper/README.txt'}
    changed = []
    for entry in previous['files']:
        if digest(ROOT / entry['path']) != entry['sha256']:
            changed.append(entry['path'])
    assert set(changed) <= allowed_changes, changed
    external_files = 0
    if external_root is not None:
        for record in records:
            for name, hash_field in [('response.bin', 'raw_response_sha256'),
                                     ('document.txt', 'text_sha256'),
                                     ('http_headers.txt', 'headers_sha256')]:
                assert digest(external_root / record['source_id'] / name) == record[hash_field]
                external_files += 1
    return {'status': 'passed', 'mode': 'external' if external_root else 'portable',
            'capture_attempts_verified': len(records), 'source_reviews_bound': len(admission['review_artifacts']),
            'prior_manifest_entries_checked': len(previous['files']),
            'prior_files_byte_identical': len(previous['files']) - len(changed),
            'permitted_current_state_changes': sorted(changed),
            'paper_claims_bound': len(ledger['claims']), 'external_files_verified': external_files,
            'source_semantics_certified_by_verifier': False, 'network_calls': 0, 'model_calls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external-root', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.external_root), sort_keys=True))

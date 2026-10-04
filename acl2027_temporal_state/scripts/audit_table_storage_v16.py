#!/usr/bin/env python3
"""Independent streaming check of retained and amended table containers.

Does not import either extractor or inspect question/reference content.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def raw_receipt(path, prefix_bytes=None):
    h, prefix, size = hashlib.sha256(), hashlib.sha256(), 0
    with gzip.open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
            if prefix_bytes is not None and size < prefix_bytes:
                prefix.update(chunk[:prefix_bytes-size])
            size += len(chunk)
    return size, h.hexdigest(), prefix.hexdigest()


def audit(old_dir, new_dir):
    inputs = ['data/reader_binding_v16/table_view_protocol_v16_1.json',
              'results/table_view_inventory_v16.json',
              'results/table_view_inventory_v16_1.json',
              'results/table_view_archive_v16_1.json']
    protocol, old, new, archive = [json.loads((ROOT/p).read_text()) for p in inputs]
    checks, failures = 0, []

    def check(value, label):
        nonlocal checks
        checks += 1
        if not value:
            failures.append(label)

    for category in ('code_bindings', 'input_bindings'):
        for path, expected in protocol[category].items():
            check(sha(ROOT/path) == expected, category+':'+path)
    check(sha(ROOT/inputs[0]) == new['protocol_sha256'], 'new_protocol_identity')
    check(sha(ROOT/inputs[1]) == archive['original_result_sha256'], 'old_result_identity')
    check(old['status'] == 'failed_sources_retained', 'original_failure_retained')
    check(Counter(r['status'] for r in old['records']) == {'completed': 9, 'failed': 3}, 'original_status_counts')
    check(new['status'] == 'completed', 'amended_status')
    check(len(new['records']) == len(old['records']) == len(archive['files']) == 12, 'denominator')
    check([r['source_path'] for r in new['records']] == [r['source_path'] for r in old['records']], 'source_order')
    archived = {r['original_filename']: r for r in archive['files']}
    totals = Counter()
    output_rows = []
    for previous, amended in zip(old['records'], new['records']):
        label = amended['source_path']
        prior = previous['external_records']
        packed = amended['external_records']
        row = archived[prior['filename']]
        old_path, new_path = old_dir/row['archive_filename'], new_dir/packed['filename']
        check(old_path.stat().st_size == row['archive_bytes'] and sha(old_path) == row['archive_sha256'], label+':archive_file')
        check(new_path.stat().st_size == packed['bytes'] and sha(new_path) == packed['sha256'], label+':new_file')
        old_size, old_hash, _ = raw_receipt(old_path)
        new_size, new_hash, prefix_hash = raw_receipt(new_path, prior['bytes'])
        check((old_size, old_hash) == (prior['bytes'], prior['sha256']), label+':lossless_old_bytes')
        check((new_size, new_hash) == (amended['storage']['uncompressed_bytes'], amended['storage']['uncompressed_sha256']), label+':new_roundtrip')
        check(new_size >= old_size and prefix_hash == old_hash, label+':original_prefix')
        if prior['complete']:
            check((old_size, old_hash) == (new_size, new_hash), label+':complete_stream_identity')
        check(amended['status'] == 'completed' and packed['complete'], label+':completion')
        check(previous['source_sha256'] == amended['source_sha256'], label+':source_identity')
        # Independent record stream recount: no numeric strings are emitted.
        counts, ordinals = Counter(), []
        with gzip.open(new_path, 'rt', encoding='utf-8') as f:
            for line in f:
                item = json.loads(line)
                if item['record_type'] == 'table':
                    counts['tables'] += 1
                    counts['rows'] += len(item['rows'])
                    counts['cells'] += sum(len(r['cells']) for r in item['rows']) + len(item['orphan_cells'])
                    counts['facts_owned_by_tables'] += len(item['fact_links'])
                    ordinals.extend(x['fact_ordinal'] for x in item['fact_links'])
                elif item['record_type'] == 'source':
                    links = item['outside_table_fact_ordinals']
                    counts['facts_outside_tables'] += len(links)
                    ordinals.extend(links)
        for key, value in counts.items():
            check(value == amended['counts'][key], label+':recount_'+key)
        # All facts must have exactly one nearest-table or outside owner.
        check(len(ordinals) == len(set(ordinals)) == amended['expected_nonfraction_count'], label+':unique_fact_coverage')
        totals.update(counts)
        output_rows.append({'source_path': label, 'original_complete': prior['complete'], 'raw_original_bytes': old_size,
                            'raw_amended_bytes': new_size, 'original_prefix_exact': prefix_hash == old_hash})
    physical = sum(p.stat().st_size for d in (old_dir, new_dir) for p in d.iterdir() if p.is_file())
    check(physical == new['combined_table_artifact_bytes'] <= 100_000_000, 'combined_disk_cap')
    return {'schema_version': 'table_storage_independent_audit_v16', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'script_sha256': sha(__file__), 'inputs': {p: sha(ROOT/p) for p in inputs},
            'status': 'passed' if not failures else 'failed', 'checks': checks, 'failures': failures,
            'counts': dict(totals), 'records': output_rows,
            'prior_attempt': {'result': 'results/table_storage_independent_audit_v16_attempt01.json',
                              'script': 'scripts/audit_table_storage_v16_attempt01.py',
                              'correction': 'Auditor initially looked for an outside_table_facts record instead of the actual source.outside_table_fact_ordinals field. It falsely flagged incomplete coverage for all twelve. Data and extraction are unchanged.'},
            'scope': 'Post-execution independent streaming provenance/container audit; no semantic, rendering, financial or QA correctness claim.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--old-dir', type=Path, required=True)
    p.add_argument('--new-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise SystemExit('refusing_existing_output')
    result = audit(args.old_dir, args.new_dir)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    print(json.dumps({k: result[k] for k in ('status','checks','failures','counts')}))
    raise SystemExit(0 if result['status'] == 'passed' else 1)

#!/usr/bin/env python3
"""Capture a frozen v10 selection, preserving failures and v08 extraction semantics.

This reads current public responses, including explicitly versioned URLs. A
versioned URL does not establish historical first public availability. No retry,
authentication, challenge handling, admission, or inference is performed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from capture_source_stream_v08 import MAX_BYTES, TIMEOUT_SECONDS, capture, now

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def safe_capture(job, fulltext_root):
    """The imported routine records ordinary curl failures; preserve outer ones."""
    history, source, index = job
    started = now()
    try:
        result = capture(job, fulltext_root)
    except Exception as exc:
        folder = fulltext_root / source['source_id']
        paths = {name: folder / filename for name, filename in
                 [('raw', 'response.bin'), ('headers', 'http_headers.txt'),
                  ('text', 'document.txt')]}
        result = {
            'source_id': source['source_id'], 'history_id': history['history_id'],
            'frame_source_index': index, 'requested_url': source['url'],
            'expected_title': source['title'], 'captured_title': None,
            'capture_started_at': started, 'retrieved_at': now(),
            'historical_first_public_availability': None,
            'reported_publication_date': source.get('reported_publication_date'),
            'publication_date_basis': source.get('publication_date_basis'),
            'event_times_in_text': {'status': 'not_annotated', 'values': []},
            'http_status': None, 'curl_returncode': None,
            'outer_failure_type': type(exc).__name__,
            'outer_failure_message': str(exc)[:2000],
            'outer_subprocess_timeout': isinstance(exc, subprocess.TimeoutExpired),
            'local_fulltext_directory': str(folder),
            'raw_response_sha256': digest(paths['raw']) if paths['raw'].exists() else None,
            'http_headers_sha256': digest(paths['headers']) if paths['headers'].exists() else None,
            'extracted_text_sha256': digest(paths['text']) if paths['text'].exists() else None,
            'raw_bytes': paths['raw'].stat().st_size if paths['raw'].exists() else 0,
            'extracted_text_bytes': paths['text'].stat().st_size if paths['text'].exists() else 0,
            'local_raw_path': str(paths['raw']) if paths['raw'].exists() else None,
            'local_headers_path': str(paths['headers']) if paths['headers'].exists() else None,
            'local_text_path': str(paths['text']) if paths['text'].exists() else None,
            'extraction': {'recipe': 'outer_failure_no_successful_receipt', 'version': 1},
            'content_usability': {
                'provisional_content_usable': False,
                'rejection_reasons': ['outer_capture_exception'],
                'semantic_content_review': 'pending', 'benchmark_admitted': False},
            'attempt_number': 1,
            'distribution_policy': 'Full external response, headers and extracted text excluded from checkpoint; hashes and capture metadata only.'}
        result['exact_version_sha256'] = result['raw_response_sha256']
        folder.mkdir(parents=True, exist_ok=True)
    result['capture_url_kind'] = source['capture_url_kind']
    result['source_role_in_discovery'] = source['source_role_in_discovery']
    result['historical_version_limit'] = source['historical_version_limit']
    result['capture_status_note'] = 'Current response capture; heuristic usability is not source eligibility adjudication.'
    write_json(fulltext_root / source['source_id'] / 'capture_receipt.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--fulltext-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    fulltext = args.fulltext_root.resolve()
    if ROOT == fulltext or ROOT in fulltext.parents:
        raise SystemExit('External full source text must stay outside the project.')
    if args.manifest.exists():
        raise SystemExit('Refusing to overwrite capture manifest; every attempt must remain identifiable.')
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    if selection.get('status') != 'frozen_before_capture':
        raise SystemExit('Selection must be frozen before network calls.')
    sources = selection['sources']
    ids = [s['source_id'] for s in sources]
    if len(set(ids)) != len(ids):
        raise SystemExit('Duplicate source IDs.')
    if any(not s['url'].startswith('https://') for s in sources):
        raise SystemExit('Only HTTPS public sources are permitted.')
    if any((fulltext / sid).exists() for sid in ids):
        raise SystemExit('Source folder already exists; refusing a retry or overwrite.')
    fulltext.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    jobs = [({'history_id': s['history_id']}, s, i) for i, s in enumerate(sources)]
    manifest = {
        'schema_version': 'correction_capture_v0.10', 'status': 'capture_running',
        'started_at': now(), 'finished_at': None,
        'selection_path': str(args.selection.resolve()), 'selection_sha256': digest(args.selection),
        'wrapper_sha256': digest(Path(__file__).resolve()),
        'extractor_path': 'scripts/capture_source_stream_v08.py',
        'extractor_sha256': digest(ROOT / 'scripts/capture_source_stream_v08.py'),
        'limits': {'curl_transfer_seconds': TIMEOUT_SECONDS, 'outer_curl_seconds': 35,
                   'max_response_bytes': MAX_BYTES, 'max_parallel_captures': 4,
                   'retries': 0, 'authentication': False, 'challenge_bypass': False},
        'environment': {}, 'source_count': len(jobs), 'records': [],
        'historical_first_public_availability': 'Not established by current response capture.',
        'content_admission': 'Separate source-only adjudication is required; no model outputs or scores.'}
    for name, command in [('curl', ['curl', '--version']), ('pdftotext', ['pdftotext', '-v'])]:
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=5)
            manifest['environment'][name] = {'returncode': run.returncode, 'version_output': (run.stdout + run.stderr)[:1500]}
        except Exception as exc:
            manifest['environment'][name] = {'error': type(exc).__name__ + ': ' + str(exc)[:300]}
    write_json(args.manifest, manifest)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(safe_capture, job, fulltext) for job in jobs]
        for future in as_completed(futures):
            result = future.result()
            manifest['records'].append(result)
            manifest['records'].sort(key=lambda r: r['frame_source_index'])
            write_json(args.manifest, manifest)
            print(json.dumps({'source_id': result['source_id'], 'http_status': result['http_status'],
                              'raw_bytes': result['raw_bytes'],
                              'usable': result['content_usability']['provisional_content_usable'],
                              'reasons': result['content_usability']['rejection_reasons']}), flush=True)
    manifest['status'] = 'capture_complete'
    manifest['finished_at'] = now()
    manifest['summary'] = {
        'attempted': len(manifest['records']),
        'provisionally_usable': sum(r['content_usability']['provisional_content_usable'] for r in manifest['records']),
        'outer_exceptions': sum('outer_failure_type' in r for r in manifest['records']),
        'retries': 0, 'benchmark_admitted': 0}
    write_json(args.manifest, manifest)


if __name__ == '__main__':
    main()

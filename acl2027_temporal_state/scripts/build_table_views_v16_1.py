#!/usr/bin/env python3
"""Storage-only amendment: unchanged frozen v16 views in deterministic gzip."""
from __future__ import annotations
import argparse
from collections import Counter
import gc
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('frozen_table_views_v16', ROOT / 'scripts/build_table_views_v16.py')
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

class CompressedBudget:
    def __init__(self, limit):
        self.limit, self.bytes = limit, 0
    def sink(self, stream):
        budget = self
        class Sink:
            def write(self, data):
                if budget.bytes + len(data) > budget.limit:
                    raise base.OutputLimitExceeded('external_compressed_byte_limit_exceeded')
                written = stream.write(data)
                budget.bytes += written
                return written
            def flush(self):
                stream.flush()
        return Sink()

def gzip_records(path, records, budget):
    raw_hash, raw_bytes = hashlib.sha256(), 0
    with path.open('xb') as stream:
        with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0,
                           fileobj=budget.sink(stream)) as compressed:
            for record in records:
                data = base.encoded(record)
                compressed.write(data)
                raw_hash.update(data)
                raw_bytes += len(data)
    return {'uncompressed_bytes': raw_bytes, 'uncompressed_sha256': raw_hash.hexdigest(),
            'compression': 'gzip_filename_empty_mtime_zero_level9'}

def decompressed_receipt(path):
    h, size = hashlib.sha256(), 0
    with gzip.open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(chunk)
            h.update(chunk)
    return {'uncompressed_bytes': size, 'uncompressed_sha256': h.hexdigest()}

def verify_original_prefix(path, original):
    remaining, h = original['bytes'], hashlib.sha256()
    with gzip.open(path, 'rb') as stream:
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise base.ViewError('amended_stream_shorter_than_original_prefix')
            h.update(chunk)
            remaining -= len(chunk)
        if original['complete'] and stream.read(1):
            raise base.ViewError('amended_complete_stream_length_changed')
    if h.hexdigest() != original['sha256']:
        raise base.ViewError('amended_stream_differs_from_original_bytes')
    return 'complete_stream_exact_match' if original['complete'] else 'partial_prefix_exact_match'

def compression_runtime():
    files = {str(Path(gzip.__file__).resolve()): base.digest(gzip.__file__)}
    binary = getattr(zlib, '__file__', None)
    if binary:
        files[str(Path(binary).resolve())] = base.digest(binary)
    return {'zlib_version': zlib.ZLIB_VERSION, 'zlib_runtime_version': zlib.ZLIB_RUNTIME_VERSION,
            'zlib_implementation': 'extension_file' if binary else 'built_into_pinned_interpreter',
            'additional_files': files}

def archive_attempt(result_path, external_dir, output):
    """Losslessly preserve private raw bytes in gzip; delete raw only after verify."""
    if output.exists():
        raise base.ViewError('refusing_to_overwrite_archive_receipt')
    if external_dir.resolve().is_relative_to(ROOT.parent.resolve()):
        raise base.ViewError('private_archive_must_be_outside_repository')
    previous = json.loads(result_path.read_text())
    targets = [r['external_records'] for r in previous['records'] if 'external_records' in r]
    # Validate every original artifact before the first mutation.
    for item in targets:
        if Path(item['filename']).name != item['filename']:
            raise base.ViewError('archive_original_basename_required')
        path = external_dir / item['filename']
        compressed = path.with_name(path.name + '.gz')
        if compressed.exists() or path.stat().st_size != item['bytes'] or base.digest(path) != item['sha256']:
            raise base.ViewError('original_archive_preflight_mismatch')
    result = {'schema_version': 'table_view_private_archive_v16_1',
              'started_at_utc': base.utc(), 'original_result_sha256': base.digest(result_path),
              'original_result_unchanged': True, 'compression_runtime': compression_runtime(),
              'action': 'lossless gzip; remove each raw file only after decompressed hash and byte verification',
              'files': []}
    for item in targets:
        path = external_dir / item['filename']
        compressed = path.with_name(path.name + '.gz')
        with path.open('rb') as source, compressed.open('xb') as target:
            with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0, fileobj=target) as zipped:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    zipped.write(chunk)
        check = decompressed_receipt(compressed)
        if check != {'uncompressed_bytes': item['bytes'], 'uncompressed_sha256': item['sha256']}:
            raise base.ViewError('lossless_archive_verification_failed')
        path.unlink()
        result['files'].append({'original_filename': item['filename'],
                                'original_bytes': item['bytes'], 'original_sha256': item['sha256'],
                                'archive_filename': compressed.name, 'archive_bytes': compressed.stat().st_size,
                                'archive_sha256': base.digest(compressed), 'restore_verified': True,
                                'original_complete': item['complete']})
    result['original_payload_bytes'] = sum(r['original_bytes'] for r in result['files'])
    result['archive_payload_bytes'] = sum(r['archive_bytes'] for r in result['files'])
    result['status'] = 'all_original_bytes_losslessly_preserved'
    result['finished_at_utc'] = base.utc()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    return result

def execute(protocol_path, source_dir, typed_dir, external_dir, output):
    if output.exists() or external_dir.exists():
        raise base.ViewError('refusing_to_overwrite_existing_attempt')
    if external_dir.resolve().is_relative_to(ROOT.parent.resolve()):
        raise base.ViewError('source_bearing_output_must_be_outside_repository')
    if output.resolve().is_relative_to(external_dir.resolve()):
        raise base.ViewError('public_inventory_must_be_outside_private_attempt')
    protocol = json.loads(protocol_path.read_text())
    errors = base.preflight(protocol, source_dir, typed_dir)
    if protocol.get('compression_runtime') != compression_runtime():
        errors.append('compression_runtime_mismatch')
    for name in ('scripts/build_table_views_v16_1.py', 'tests/test_table_views_storage_v16_1.py'):
        if name not in protocol.get('code_bindings', {}):
            errors.append('storage_amendment_code_binding_missing:' + name)
    if protocol.get('storage_amendment', {}).get('extraction_logic_changed') is not False:
        errors.append('unchanged_extraction_declaration_missing')
    amendment = protocol.get('storage_amendment', {})
    archive_dir = Path(amendment.get('archive_directory', '/nonexistent'))
    prior_bytes = amendment.get('archived_prior_attempt_total_bytes', -1)
    if not archive_dir.is_dir() or sum(p.stat().st_size for p in archive_dir.iterdir() if p.is_file()) != prior_bytes:
        errors.append('prior_archive_directory_bytes_mismatch')
    archive_receipt = json.loads((ROOT / 'results/table_view_archive_v16_1.json').read_text())
    for row in archive_receipt['files']:
        path = archive_dir / row['archive_filename']
        if not path.is_file() or path.stat().st_size != row['archive_bytes'] or base.digest(path) != row['archive_sha256']:
            errors.append('prior_archive_hash_mismatch:' + row['archive_filename'])
    original_result = json.loads((ROOT / 'results/table_view_inventory_v16.json').read_text())
    originals = {r['source_path']: r['external_records'] for r in original_result['records']}
    result = {'schema_version': 'table_view_inventory_v16_1', 'started_at_utc': base.utc(),
              'protocol_sha256': base.digest(protocol_path), 'status': 'preflight_failed' if errors else 'running',
              'preflight_errors': errors, 'source_denominator': len(protocol['sources']),
              'expected_nonfraction_denominator': sum(s['expected_nonfraction_count'] for s in protocol['sources']),
              'storage_amendment': protocol.get('storage_amendment'),
              'natural_QA_predictions': 0, 'model_calls': 0, 'question_selection_performed': False,
              'rendering_verified': False, 'extraction_logic_changed': False,
              'independent_parser_scope': 'lxml locators/attributes versus frozen Expat helper; shared helper lineage disclosed',
              'records': [], 'totals': {}}
    if not errors:
        external_dir.mkdir(parents=True)
        (external_dir / 'attempt_started.json').write_bytes(base.encoded({'started_at_utc': result['started_at_utc'],
            'protocol_sha256': result['protocol_sha256']}))
        budget = CompressedBudget(base.MAX_EXTERNAL_BYTES - base.PRIVATE_RESERVE - prior_bytes)
        aggregate = Counter()
        for item in protocol['sources']:
            local = external_dir / (Path(item['external_filename']).stem + '.tables.jsonl.gz')
            entry = {'source_path': item['source_path'], 'source_sha256': item['expected_sha256'],
                     'source_bytes': item['expected_bytes'],
                     'expected_nonfraction_count': item['expected_nonfraction_count'], 'status': 'running'}
            try:
                source = (source_dir / item['external_filename']).read_bytes()
                typed = base.load_typed(typed_dir / item['typed_filename'])
                if len(typed) != item['expected_nonfraction_count']:
                    raise base.ViewError('frozen_nonfraction_count_mismatch')
                records, summary = base.extract_views(source, typed, source_path=item['source_path'],
                                                      source_sha256=item['expected_sha256'])
                storage = gzip_records(local, records, budget)
                if decompressed_receipt(local) != {k: storage[k] for k in ('uncompressed_bytes', 'uncompressed_sha256')}:
                    raise base.ViewError('compressed_output_roundtrip_mismatch')
                lineage = verify_original_prefix(local, originals[item['source_path']])
                entry.update(summary)
                entry['storage'] = storage
                entry['original_attempt_lineage'] = lineage
                entry['status'] = 'completed'
                aggregate.update(summary['counts'])
                del source, typed, records, summary
            except Exception as exc:
                entry['status'] = 'failed'
                entry['error_class'] = type(exc).__name__
                entry['failure_code'] = str(exc) if isinstance(exc, base.ViewError) else 'technical_exception'
            finally:
                if local.exists():
                    entry['external_records'] = {'filename': local.name, 'bytes': local.stat().st_size,
                        'sha256': base.digest(local), 'complete': entry['status'] == 'completed', 'container': 'gzip_jsonl'}
                result['records'].append(entry)
                gc.collect()
        result['totals'] = dict(aggregate)
        result['status'] = 'completed' if all(r['status'] == 'completed' for r in result['records']) else 'failed_sources_retained'
        result['external_payload_bytes'] = budget.bytes
        result['external_output_limit_bytes'] = base.MAX_EXTERNAL_BYTES
        (external_dir / 'attempt_finished.json').write_bytes(base.encoded({'status': result['status'],
            'finished_at_utc': base.utc(), 'completed_sources': sum(r['status'] == 'completed' for r in result['records'])}))
        result['external_total_bytes'] = sum(p.stat().st_size for p in external_dir.iterdir() if p.is_file())
        result['archived_prior_attempt_total_bytes'] = prior_bytes
        result['combined_table_artifact_bytes'] = prior_bytes + result['external_total_bytes']
        if result['combined_table_artifact_bytes'] > base.MAX_EXTERNAL_BYTES:
            result['status'] = 'external_limit_violation'
    result['finished_at_utc'] = base.utc()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'source_denominator', 'totals')}, sort_keys=True))
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--typed-records', type=Path, required=True)
    parser.add_argument('--external-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = execute(args.protocol, args.sources, args.typed_records, args.external_output, args.output)
    raise SystemExit(0 if result['status'] == 'completed' else 1)

if __name__ == '__main__':
    main()

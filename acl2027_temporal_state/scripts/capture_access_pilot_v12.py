#!/usr/bin/env python3
"""Freeze, then capture eight public URLs once; full contents stay external."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from capture_source_stream_v08 import DocumentText, decode_html, normalized, usability

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
DEFAULT_EXTERNAL = Path('/workspace/scratch/bdef663e3dfc/tmp/access_pilot_v12')
LIMITS = {'sources': 8, 'attempts_per_url': 1, 'retries': 0,
          'https_only': True, 'max_redirects': 5, 'connect_seconds': 10,
          'transfer_seconds': 30, 'outer_curl_seconds': 35,
          'max_response_bytes': 8 * 1024 * 1024, 'pdf_extraction_seconds': 20,
          'implicit_curl_config': False, 'authentication': False}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write_json(path, value, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def validate_protocol(protocol):
    if not protocol.get('frozen_at_utc'):
        raise ValueError('Protocol must carry a pre-execution freeze timestamp.')
    expected = {'max_attempts_per_url': 1, 'network_timeout_seconds': 30,
                'connect_timeout_seconds': 10, 'maximum_payload_bytes': 8388608,
                'maximum_redirects': 5, 'parallelism': 1}
    if any(protocol.get(k) != v for k, v in expected.items()):
        raise ValueError('Protocol guard values differ from the collector.')
    sources = protocol.get('sources', [])
    if len(sources) != LIMITS['sources']:
        raise ValueError('Exactly eight frozen sources are required.')
    ids, urls = [], []
    for source in sources:
        for key in ('source_id', 'history_id', 'url'):
            if not isinstance(source.get(key), str) or not source[key].strip():
                raise ValueError('Source needs a nonempty ' + key)
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', source['source_id']):
            raise ValueError('Unsafe source ID.')
        url = urlsplit(source['url'])
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.password is not None or url.fragment):
            raise ValueError('Only public HTTPS URLs without credentials/fragments.')
        ids.append(source['source_id'])
        urls.append(source['url'])
    if len(set(ids)) != len(ids) or len(set(urls)) != len(urls):
        raise ValueError('Duplicate source ID or URL.')
    return sources


def tool_versions():
    result = {}
    for name, flag in [('curl', '--version'), ('pdftotext', '-v')]:
        executable = shutil.which(name)
        if executable is None:
            raise RuntimeError(name + ' is required before freezing.')
        run = subprocess.run([executable, flag], capture_output=True, text=True, timeout=5)
        output = (run.stdout + run.stderr).strip()
        if run.returncode:
            raise RuntimeError(name + ' version query failed.')
        result[name] = {'executable': str(Path(executable).resolve()),
                        'executable_sha256': digest(Path(executable).resolve()),
                        'version_output': output}
    match = re.match(r'curl (\d+)\.(\d+)\.(\d+)', result['curl']['version_output'])
    if not match or tuple(map(int, match.groups())) < (8, 4, 0):
        raise RuntimeError('curl >=8.4.0 required for streaming --max-filesize.')
    result['python'] = {'version': sys.version, 'executable': sys.executable}
    return result


def external_path(value):
    result = value.resolve()
    if result == REPOSITORY or REPOSITORY in result.parents:
        raise ValueError('Raw captures must stay outside the repository.')
    return result


def freeze(protocol_path, frozen_path, manifest_path, external):
    external = external_path(external)
    protocol_path = protocol_path.resolve()
    protocol = json.loads(protocol_path.read_text())
    validate_protocol(protocol)
    if external.exists() or frozen_path.exists() or manifest_path.exists():
        raise FileExistsError('Refusing overwrite/retry of an existing attempt.')
    tools = tool_versions()  # Local executable queries only; no network.
    dependencies = [Path(__file__).resolve(), ROOT / 'scripts/capture_source_stream_v08.py']
    frozen = {'schema_version': 'access_capture_v12.freeze.1', 'frozen_at': now(),
              'protocol_path': str(protocol_path), 'protocol_sha256': digest(protocol_path),
              'protocol': protocol, 'limits': LIMITS, 'tools': tools,
              'external_root': str(external), 'manifest_path': str(manifest_path.resolve()),
              'code': [{'path': str(p), 'sha256': digest(p)} for p in dependencies],
              'source_content_distribution': 'External only; packaged receipt is allowlisted metadata.',
              'network_calls_before_freeze': 0}
    external.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol_path, external / 'protocol.json')
    for path in dependencies:
        shutil.copyfile(path, external / path.name)
    write_json(external / 'frozen_inputs.json', frozen, exclusive=True)
    write_json(frozen_path, frozen, exclusive=True)
    return frozen


def verify_frozen(frozen_path):
    frozen = json.loads(frozen_path.read_text())
    if frozen['limits'] != LIMITS:
        raise RuntimeError('Frozen limits differ from this collector.')
    validate_protocol(frozen['protocol'])
    protocol_path = Path(frozen['protocol_path'])
    if digest(protocol_path) != frozen['protocol_sha256']:
        raise RuntimeError('Protocol changed after freeze.')
    for entry in frozen['code']:
        if digest(Path(entry['path'])) != entry['sha256']:
            raise RuntimeError('Collector/extractor changed after freeze.')
    if frozen['tools'] != tool_versions():
        raise RuntimeError('Runtime/tool versions changed after freeze.')
    external = external_path(Path(frozen['external_root']))
    if json.loads((external / 'frozen_inputs.json').read_text()) != frozen:
        raise RuntimeError('External frozen receipt differs.')
    if digest(external / 'protocol.json') != frozen['protocol_sha256']:
        raise RuntimeError('External protocol snapshot changed.')
    for entry in frozen['code']:
        if digest(external / Path(entry['path']).name) != entry['sha256']:
            raise RuntimeError('External code snapshot changed.')
    return frozen


def curl_command(source, folder, tools):
    return [tools['curl']['executable'], '-q', '--silent', '--show-error',
            '--location', '--max-redirs', str(LIMITS['max_redirects']),
            '--proto', '=https', '--proto-redir', '=https',
            '--connect-timeout', str(LIMITS['connect_seconds']),
            '--max-time', str(LIMITS['transfer_seconds']),
            '--max-filesize', str(LIMITS['max_response_bytes']),
            '--user-agent', 'TemporalStateResearch/0.12 (public document research; no authentication)',
            '--dump-header', str(folder / 'http_headers.txt'),
            '--output', str(folder / 'response.bin'), '--write-out', '%{json}',
            source['url']]


def external_bytes(value):
    if value is None:
        return b''
    return value if isinstance(value, bytes) else value.encode('utf-8', errors='replace')


def parse_document(raw, content_type, tools):
    payload = raw.read_bytes()
    title = ''
    if payload.startswith(b'%PDF-') or 'application/pdf' in content_type.lower():
        run = subprocess.run([tools['pdftotext']['executable'], '-enc', 'UTF-8', '-eol',
                              'unix', str(raw), '-'], capture_output=True,
                             timeout=LIMITS['pdf_extraction_seconds'], check=True)
        value = run.stdout.decode('utf-8', errors='replace')
        text = '\n'.join(x for x in (normalized(z) for z in value.splitlines()) if x) + '\n'
        recipe = {'name': 'pdftotext_reading_order_NFC_nonempty_lines', 'version': 1,
                  'pdftotext_version': tools['pdftotext']['version_output'],
                  'pdftotext_executable_sha256': tools['pdftotext']['executable_sha256']}
    elif 'html' in content_type.lower() or payload.lstrip().lower().startswith((b'<!', b'<html')):
        decoded, charset = decode_html(payload, content_type)
        parser = DocumentText()
        parser.feed(decoded)
        parser.close()
        text, title = parser.text(), normalized(''.join(parser.titles))
        recipe = {'name': 'stdlib_HTMLParser_body_text_NFC_nonempty_lines', 'version': 2,
                  'charset': charset, 'excluded_tags': sorted(DocumentText.SKIP)}
    elif content_type.lower().startswith('text/plain'):
        value = payload.decode('utf-8', errors='replace')
        text = '\n'.join(x for x in (normalized(z) for z in value.splitlines()) if x) + '\n'
        recipe = {'name': 'UTF8_NFC_nonempty_lines', 'version': 1}
    else:
        raise ValueError('unsupported_content_type')
    return text, title, recipe


def capture_one(source, index, external, tools):
    folder = external / source['source_id']
    folder.mkdir(exist_ok=False)
    started = now()
    command = curl_command(source, folder, tools)
    write_json(folder / 'request.json', {'command': command, 'started_at': started}, exclusive=True)
    raw, text_path = folder / 'response.bin', folder / 'document.txt'
    private = {'returncode': None, 'transfer': {}, 'failure': None}
    try:
        run = subprocess.run(command, capture_output=True, timeout=LIMITS['outer_curl_seconds'])
        private['returncode'] = run.returncode
        (folder / 'curl.stdout').write_bytes(run.stdout)
        (folder / 'curl.stderr').write_bytes(run.stderr)
        try:
            value = json.loads(run.stdout)
            private['transfer'] = value if isinstance(value, dict) else {}
        except (ValueError, UnicodeError):
            private['failure'] = 'invalid_curl_writeout'
    except Exception as exc:
        private['failure'] = type(exc).__name__
        (folder / 'outer_error.txt').write_text(str(exc), encoding='utf-8')
        (folder / 'curl.stdout').write_bytes(external_bytes(getattr(exc, 'stdout', None)))
        (folder / 'curl.stderr').write_bytes(external_bytes(getattr(exc, 'stderr', None)))
    transfer = private['transfer']
    raw_bytes = raw.stat().st_size if raw.exists() else 0
    transport_ok = private['returncode'] == 0 and private['failure'] is None and raw_bytes <= LIMITS['max_response_bytes']
    content_type = transfer.get('content_type') or ''
    text, title, recipe, extraction_error = '', '', {'name': 'not_extracted'}, None
    if transport_ok and raw.exists():
        try:
            text, title, recipe = parse_document(raw, content_type, tools)
            text_path.write_text(text, encoding='utf-8', newline='\n')
        except Exception as exc:
            extraction_error = type(exc).__name__
            (folder / 'extraction_error.txt').write_text(str(exc), encoding='utf-8')
    heuristic = usability(transfer.get('http_code', 0), content_type, title, text, source.get('title', ''))
    if not source.get('title'):
        heuristic['rejection_reasons'] = [x for x in heuristic['rejection_reasons']
            if x != 'expected_title_lexical_coverage_below_0_6']
        heuristic['provisional_content_usable'] = not heuristic['rejection_reasons']
        heuristic['review_warnings'].append('expected_title_not_supplied_no_lexical_identity_test')
    parse_ok = transport_ok and extraction_error is None and bool(text.strip())
    record = {'source_id': source['source_id'], 'history_id': source['history_id'],
              'source_index': index, 'attempt_number': 1, 'requested_url': source['url'],
              'started_at': started, 'finished_at': now(),
              'curl_returncode': private['returncode'], 'outer_failure_type': private['failure'],
              'http_status': transfer.get('http_code'), 'content_type': content_type,
              'redirect_count': transfer.get('num_redirects'),
              'raw_bytes': raw_bytes, 'raw_response_sha256': digest(raw) if raw.exists() else None,
              'headers_sha256': digest(folder / 'http_headers.txt') if (folder / 'http_headers.txt').exists() else None,
              'text_bytes': text_path.stat().st_size if text_path.exists() else 0,
              'text_sha256': digest(text_path) if text_path.exists() else None,
              'transport_success': transport_ok,
              'http_success': transport_ok and transfer.get('http_code') == 200,
              'mechanical_parse_success': parse_ok, 'extraction': recipe,
              'extraction_error_type': extraction_error,
              'provisional_usability': transport_ok and parse_ok and heuristic['provisional_content_usable'],
              'heuristic_rejection_reasons': heuristic['rejection_reasons'],
              'heuristic_review_warnings': heuristic['review_warnings'],
              'extracted_word_count': heuristic['word_count'],
              'expected_title_lexical_coverage': heuristic['expected_title_lexical_coverage'] if source.get('title') else None,
              'expected_title_check_applicable': bool(source.get('title')),
              'response_within_byte_cap': raw_bytes <= LIMITS['max_response_bytes'],
              'complete_source_verified': False, 'completeness_review': 'pending_independent_source_review',
              'historical_version_verified': False, 'benchmark_admitted': False}
    write_json(folder / 'private_transfer_receipt.json', private, exclusive=True)
    write_json(folder / 'capture_receipt.json', record, exclusive=True)
    return record


def execute(frozen_path):
    frozen = verify_frozen(frozen_path)
    external = Path(frozen['external_root'])
    manifest_path = Path(frozen['manifest_path'])
    sources = frozen['protocol']['sources']
    if manifest_path.exists() or any((external / s['source_id']).exists() for s in sources):
        raise FileExistsError('Refusing retry/overwrite: an attempt already started.')
    manifest = {'schema_version': 'access_capture_v12.results.1', 'status': 'running',
                'started_at': now(), 'finished_at': None, 'frozen_inputs_sha256': digest(frozen_path),
                'protocol_sha256': frozen['protocol_sha256'], 'limits': LIMITS, 'records': [],
                'source_content_distribution': 'No fulltext, response headers or cookies in this manifest.'}
    write_json(manifest_path, manifest, exclusive=True)
    for index, source in enumerate(sources):
        record = capture_one(source, index, external, frozen['tools'])
        manifest['records'].append(record)
        write_json(manifest_path, manifest)
        print(json.dumps({key: record[key] for key in
                          ('source_id', 'curl_returncode', 'http_status', 'raw_bytes',
                           'transport_success', 'mechanical_parse_success', 'provisional_usability')}), flush=True)
    manifest.update(status='complete', finished_at=now(), summary={
        'attempted_urls': len(manifest['records']), 'retries': 0,
        **{key: sum(r[key] for r in manifest['records']) for key in
           ('transport_success', 'http_success', 'mechanical_parse_success', 'provisional_usability')},
        'complete_sources_verified': 0, 'benchmark_admitted': 0})
    write_json(manifest_path, manifest)
    write_json(external / 'manifest.json', manifest, exclusive=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['freeze', 'execute', 'verify'])
    parser.add_argument('--protocol', type=Path, default=ROOT / 'data/access_pilot_v12/protocol.json')
    parser.add_argument('--frozen', type=Path, default=ROOT / 'data/access_pilot_v12/frozen_inputs.json')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'results/access_pilot_v12.json')
    parser.add_argument('--external', type=Path, default=DEFAULT_EXTERNAL)
    args = parser.parse_args()
    if args.mode == 'freeze':
        result = freeze(args.protocol, args.frozen, args.manifest, args.external)
        print(json.dumps({'status': 'frozen_no_network', 'frozen_path': str(args.frozen),
                          'protocol_sha256': result['protocol_sha256']}))
    elif args.mode == 'execute':
        result = execute(args.frozen)
        print(json.dumps(result['summary']))
    else:
        verify_frozen(args.frozen)
        print(json.dumps({'status': 'frozen_inputs_verified_no_network'}))


if __name__ == '__main__':
    main()

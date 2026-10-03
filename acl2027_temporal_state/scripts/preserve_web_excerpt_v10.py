#!/usr/bin/env python3
"""Preserve supplied web-tool objects verbatim; does not acquire source content."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(value):
    return sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--external-root', type=Path, required=True)
    parser.add_argument('--record-json', required=True)
    args = parser.parse_args()
    external = args.external_root.resolve()
    if external == ROOT or ROOT in external.parents:
        raise SystemExit('Public-source excerpts must remain outside the distributable project.')
    record = json.loads(args.record_json)
    sid, call_index = record['source_id'], record['call_index']
    if not sid.replace('_', '').isalnum() or not 1 <= call_index <= 4:
        raise SystemExit('Invalid source ID or call index.')
    folder = external / sid
    folder.mkdir(parents=True, exist_ok=True)
    prefix = f'call_{call_index:02d}'
    request_path, response_path = folder / f'{prefix}_request.json', folder / f'{prefix}_response.json'
    if request_path.exists() or response_path.exists():
        raise SystemExit('Refusing to overwrite existing web-tool evidence.')
    request_bytes = (json.dumps(record['request'], ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    response_bytes = (json.dumps(record['response'], ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    request_path.write_bytes(request_bytes)
    response_path.write_bytes(response_bytes)
    document_path = folder / 'document.txt'
    old = document_path.read_bytes() if document_path.exists() else b''
    document = old.decode('utf-8')
    spans = []
    for index, block in enumerate(record['response'].get('content', [])):
        if block.get('type') != 'text':
            continue
        marker = f'\n[[SYNTHETIC WEB-TOOL EXCERPT BOUNDARY: {prefix}_response.json content[{index}].text]]\n'
        document += marker
        start = len(document)
        byte_start = len(document.encode('utf-8'))
        document += block['text']
        spans.append({'content_block_index': index,
                      'json_pointer': f'/content/{index}/text',
                      'start_character': start, 'end_character': len(document),
                      'start_byte': byte_start, 'end_byte': len(document.encode('utf-8')),
                      'text_sha256': sha(block['text'].encode('utf-8'))})
    document_path.write_bytes(document.encode('utf-8'))
    receipt = {
        'source_id': sid, 'call_index': call_index,
        'started_at': record['started_at'], 'ended_at': record['ended_at'],
        'request_path': str(request_path), 'response_path': str(response_path),
        'request_sha256': sha(request_bytes), 'response_sha256': sha(response_bytes),
        'request_bytes': len(request_bytes), 'response_bytes': len(response_bytes),
        'document_path': str(document_path), 'spans': spans,
        'text_block_count': len(spans),
        'non_text_block_types': [b.get('type') for b in record['response'].get('content', []) if b.get('type') != 'text'],
        'response_is_error': record['response'].get('isError'),
        'representation': 'Exact web-tool JSON object serialized as UTF-8 JSON; text blocks concatenated without text edits, with explicitly synthetic boundaries. Not original publisher bytes or full source text.'}
    receipt_path = folder / f'{prefix}_receipt.json'
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'source_id': sid, 'call_index': call_index,
                      'receipt_path': str(receipt_path), 'document_path': str(document_path),
                      'response_sha256': receipt['response_sha256'], 'text_block_count': len(spans)}))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Offline structural/evidence validation, never semantic entailment certification."""
import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / 'configs/source_annotation_v08_schema.json'
REQUEST_FIELDS = {'schema_version', 'history_id', 'prefix_id', 'delivery_batch_index',
                  'capture_manifest_sha256', 'delivery_manifest_sha256', 'sources'}
SOURCE_FIELDS = {'source_id', 'exact_version_sha256', 'text_sha256', 'text'}
SHA = re.compile(r'^[0-9a-f]{64}$')
ID = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]*$')


def digest(raw):
    return sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    def bad(value):
        raise ValueError(f'non-finite JSON value: {value}')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def schema_check(value, spec, where='$'):
    """The deliberately small closed JSON-schema vocabulary used by this artifact."""
    if 'anyOf' in spec:
        for option in spec['anyOf']:
            try:
                schema_check(value, option, where)
                return
            except ValueError:
                pass
        raise ValueError(f'{where}: does not match any allowed shape')
    if 'const' in spec:
        require(value == spec['const'], f'{where}: unexpected constant')
    if 'enum' in spec:
        require(value in spec['enum'], f'{where}: unexpected enumerated value')
    kind = spec.get('type')
    if kind == 'object':
        require(isinstance(value, dict), f'{where}: expected object')
        require(set(value) == set(spec['required']), f'{where}: unexpected or missing fields')
        for key, child in spec['properties'].items():
            schema_check(value[key], child, f'{where}.{key}')
    elif kind == 'array':
        require(isinstance(value, list), f'{where}: expected array')
        require(len(value) >= spec.get('minItems', 0), f'{where}: too few items')
        for index, item in enumerate(value):
            schema_check(item, spec['items'], f'{where}[{index}]')
    elif kind == 'string':
        require(isinstance(value, str), f'{where}: expected string')
        require(len(value) >= spec.get('minLength', 0), f'{where}: empty string')
        if 'pattern' in spec:
            require(re.fullmatch(spec['pattern'], value) is not None, f'{where}: invalid string format')
    elif kind == 'integer':
        require(type(value) is int, f'{where}: expected integer, not boolean')
        require(value >= spec.get('minimum', value), f'{where}: integer below minimum')
    elif kind == 'null':
        require(value is None, f'{where}: expected null')


def _validate_request(request):
    require(isinstance(request, dict) and set(request) == REQUEST_FIELDS,
            'request: unexpected or missing fields (possible context leakage)')
    require(request['schema_version'] == 'source_annotation_request_v0.8', 'request: invalid version')
    for key in ('history_id', 'prefix_id'):
        require(isinstance(request[key], str) and ID.fullmatch(request[key]), f'request: invalid {key}')
    require(type(request['delivery_batch_index']) is int and request['delivery_batch_index'] >= 1,
            'request: delivery_batch_index must be a positive integer')
    for key in ('capture_manifest_sha256', 'delivery_manifest_sha256'):
        require(isinstance(request[key], str) and SHA.fullmatch(request[key]), f'request: invalid {key}')
    require(isinstance(request['sources'], list) and request['sources'], 'request: no sources')
    seen = set()
    for source in request['sources']:
        require(isinstance(source, dict) and set(source) == SOURCE_FIELDS, 'request source: unexpected fields')
        sid = source['source_id']
        require(isinstance(sid, str) and ID.fullmatch(sid) and sid not in seen,
                'request source: invalid/duplicate source ID')
        seen.add(sid)
        require(isinstance(source['text'], str) and source['text'].strip(), 'request source: missing text')
        for key in ('exact_version_sha256', 'text_sha256'):
            require(isinstance(source[key], str) and SHA.fullmatch(source[key]), f'request source: invalid {key}')
        require(digest(source['text'].encode('utf-8')) == source['text_sha256'], 'request source: text hash mismatch')


def _validate_manifest_bindings(request, capture_raw, delivery_raw):
    require(digest(capture_raw) == request['capture_manifest_sha256'], 'capture manifest hash mismatch')
    require(digest(delivery_raw) == request['delivery_manifest_sha256'], 'delivery manifest hash mismatch')
    capture = strict_json(capture_raw)
    delivery = strict_json(delivery_raw)
    capture_sources = capture.get('sources', [])
    require(isinstance(capture_sources, list), 'capture manifest: invalid sources')
    by_source = {}
    for source in capture_sources:
        sid = source.get('source_id')
        require(sid not in by_source, 'capture manifest: duplicate source ID')
        by_source[sid] = source
    for source in request['sources']:
        original = by_source.get(source['source_id'])
        require(original is not None, 'request source absent from capture manifest')
        require(original.get('history_id') == request['history_id'], 'capture source belongs to another history')
        require(original.get('exact_version_sha256') == source['exact_version_sha256'], 'captured response hash mismatch')
        require(original.get('extracted_text_sha256') == source['text_sha256'], 'captured text hash mismatch')
        require(original.get('content_usability', {}).get('provisional_content_usable') is True,
                'capture source is not provisionally usable')
    # Deliberately read only explicit prefix records, never infer historical availability.
    prefixes = delivery.get('prefixes', [])
    require(isinstance(prefixes, list), 'delivery manifest: invalid prefixes')
    matches = [p for p in prefixes if p.get('prefix_id') == request['prefix_id']]
    require(len(matches) == 1, 'delivery manifest: missing or duplicate prefix ID')
    prefix = matches[0]
    require(prefix.get('status') == 'ready', 'delivery prefix is not ready for annotation')
    require(prefix.get('history_id') == request['history_id'], 'delivery prefix history mismatch')
    require(prefix.get('delivery_batch_index') == request['delivery_batch_index'], 'delivery prefix batch mismatch')
    require(prefix.get('source_ids') == [s['source_id'] for s in request['sources']],
            'request sources differ from frozen delivery prefix')
    expected_bindings = [{k: s[k] for k in ('source_id', 'exact_version_sha256', 'text_sha256')}
                         for s in request['sources']]
    require(prefix.get('source_bindings') == expected_bindings, 'delivery source bindings mismatch')
    require(prefix.get('missing_source_ids') == [], 'ready prefix contains missing sources')


def validate(request_raw, output_raw, capture_raw=None, delivery_raw=None):
    request = strict_json(request_raw)
    output = strict_json(output_raw)
    _validate_request(request)
    schema_check(output, strict_json(SCHEMA.read_bytes()))
    require(output['request_sha256'] == digest(request_raw), 'annotation request hash mismatch')
    for key in ('history_id', 'prefix_id'):
        require(output[key] == request[key], f'annotation {key} mismatch')
    source_ids = [s['source_id'] for s in request['sources']]
    require(output['eligible_source_ids'] == source_ids, 'annotation eligible source IDs/order mismatch')
    require((capture_raw is None) == (delivery_raw is None), 'supply both manifests or neither')
    if capture_raw is not None:
        _validate_manifest_bindings(request, capture_raw, delivery_raw)
    source_text = {s['source_id']: s['text'] for s in request['sources']}
    span_count = 0

    def spans(rows):
        nonlocal span_count
        for row in rows:
            require(row['source_id'] in source_text, 'evidence cites an ineligible or future source')
            text = source_text[row['source_id']]
            require(row['start'] < row['end'] <= len(text), 'evidence character offsets out of bounds')
            require(text[row['start']:row['end']] == row['quote'], 'evidence quote/span mismatch')
            span_count += 1

    ids = set()
    def unique(value):
        require(value not in ids, 'duplicate annotation ID')
        ids.add(value)
    claims = {}
    for claim in output['claims']:
        unique(claim['claim_id'])
        claims[claim['claim_id']] = claim
        spans(claim['evidence'])
        for constraint in claim['temporal_constraints']:
            spans(constraint['evidence'])
            for key in ('lower', 'upper'):
                value = constraint[key]
                if value is not None:
                    try:
                        parsed = date.fromisoformat(value)
                    except (ValueError, TypeError) as exc:
                        raise ValueError('temporal bound must be a canonical ISO date or null') from exc
                    require(parsed.isoformat() == value, 'noncanonical temporal date')
            lower, upper = constraint['lower'], constraint['upper']
            require(not (lower and upper) or lower <= upper, 'reversed temporal bounds')
            if constraint['precision'] == 'day':
                require(lower is not None and lower == upper, 'day precision needs one exact date')
            if constraint['precision'] in {'relative', 'unknown'}:
                require(lower is None and upper is None, 'unresolved temporal constraint cannot invent dates')
    correction_pairs = set()
    for correction in output['corrections']:
        unique(correction['correction_id'])
        spans(correction['evidence'])
        target = correction['target_claim_id']
        corrected = correction['corrected_claim_id']
        require(corrected in claims, 'correction refers to missing corrected claim')
        require(claims[corrected]['assertion_status'] == 'asserted_by_source',
                'corrected claim must be asserted by this source')
        if target is not None:
            require(target in claims and target != corrected, 'invalid correction target claim')
            correction_pairs.add((corrected, target))
    for link in output['links']:
        unique(link['link_id'])
        spans(link['evidence'])
        start, end = link['from_claim_id'], link['to_claim_id']
        require(start in claims and end in claims and start != end, 'invalid link endpoints')
        if link['type'] == 'corrects':
            require((start, end) in correction_pairs, 'corrects link lacks matching explicit correction record')
    for unknown in output['unknowns']:
        spans(unknown['evidence'])
    for limit in output['representation_limits']:
        require(len(set(limit['claim_ids'])) == len(limit['claim_ids']), 'duplicate representation limit claim IDs')
        require(set(limit['claim_ids']) <= set(claims), 'representation limit refers to missing claim')
    return {
        'schema_version': 'source_annotation_validation_v0.8',
        'status': 'valid_structure_evidence_binding_not_semantic_truth',
        'history_id': request['history_id'], 'prefix_id': request['prefix_id'],
        'request_sha256': digest(request_raw), 'annotation_sha256': digest(output_raw),
        'schema_sha256': digest(SCHEMA.read_bytes()), 'manifest_bindings_checked': capture_raw is not None,
        'source_count': len(source_ids), 'claim_count': len(claims),
        'correction_count': len(output['corrections']), 'link_count': len(output['links']),
        'unknown_count': len(output['unknowns']), 'evidence_span_count': span_count,
        'interpretation': 'Unversioned model source-only annotations; no gold, completeness, semantic or historical-availability certification.'
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--annotation', type=Path, required=True)
    parser.add_argument('--capture-manifest', type=Path)
    parser.add_argument('--delivery-manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = validate(args.request.read_bytes(), args.annotation.read_bytes(),
                      args.capture_manifest.read_bytes() if args.capture_manifest else None,
                      args.delivery_manifest.read_bytes() if args.delivery_manifest else None)
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    print(rendered, end='')


if __name__ == '__main__':
    main()

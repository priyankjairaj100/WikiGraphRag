#!/usr/bin/env python3
"""Prepare isolated source-only requests, without a model, network or labels.

Default writes a text-free provenance manifest. --emit-pilot writes the exact
source request to a user-selected intermediate path; do not package this duplicate
source-text file. No annotation, question, expectation or results file is read.
"""
import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/natural_protocol_v06.json'
SOURCE_FIELDS = {'source_id', 'history_id', 'text_sha256',
                 'reported_publication_date', 'operational_available_at',
                 'availability_basis', 'text'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return sha256(value).hexdigest()


def strict_json(raw):
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out:
                raise ValueError('Duplicate JSON field: ' + key)
            out[key] = value
        return out
    def constant(value):
        raise ValueError('Non-finite JSON value: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def valid_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Dates must be exact ISO days')


def validate_pack(content, spec):
    if set(content) != {'purpose', 'prefixes', 'sources'}:
        raise ValueError('Unexpected source-pack fields')
    if content['prefixes'] != spec['history_prefixes']:
        raise ValueError('Prefix metadata changed')
    if not isinstance(content['sources'], list):
        raise ValueError('Sources must be an array')
    seen = set()
    for source in content['sources']:
        if set(source) != SOURCE_FIELDS or any(not isinstance(v, str) for v in source.values()):
            raise ValueError('Unexpected source fields or types')
        sid = source['source_id']
        if not sid or sid in seen:
            raise ValueError('Duplicate or empty source ID')
        seen.add(sid)
        history = source['history_id']
        if history not in content['prefixes']:
            raise ValueError('Source history not declared in prefix metadata')
        for field in ('reported_publication_date', 'operational_available_at'):
            valid_day(source[field])
        cutoff = content['prefixes'][history]
        valid_day(cutoff)
        if source['operational_available_at'] > cutoff:
            raise ValueError('Future source in prefix')
        if digest(source['text'].encode('utf-8')) != source['text_sha256']:
            raise ValueError('Source text hash changed')
    return content


def build_requests():
    config = strict_json(CONFIG.read_text())
    artifacts = {}
    for name in ('candidate_prompt', 'candidate_schema', 'scorer_prompt', 'scorer_schema'):
        path = ROOT / config[name]
        artifacts[name] = {'path': config[name], 'sha256': digest(path.read_bytes())}
        if name.endswith('schema'):
            strict_json(path.read_text())
    requests, packs, seen = [], [], set()
    for spec in config['source_packs']:
        path = ROOT / spec['path']
        raw = path.read_bytes()
        if digest(raw) != spec['sha256']:
            raise ValueError('Configured source-pack hash changed')
        content = validate_pack(strict_json(raw), spec)
        packs.append({'path': spec['path'], 'sha256': spec['sha256']})
        for history, cutoff in sorted(content['prefixes'].items()):
            rid = history + '__' + cutoff
            if rid in seen:
                raise ValueError('Duplicate history-prefix request')
            seen.add(rid)
            sources = sorted((s for s in content['sources'] if s['history_id'] == history),
                             key=lambda s: (s['operational_available_at'], s['source_id']))
            if not sources:
                raise ValueError('Empty history-prefix request')
            request = {'schema_version': 'source_request_v0.6', 'request_id': rid,
                       'history_id': history, 'information_cutoff': cutoff,
                       'extraction_domains': config['extraction_domains'],
                       'scope_filter': None, 'sources': sources}
            requests.append(request)
    return config, artifacts, packs, sorted(requests, key=lambda x: x['request_id'])


def pilot_request(config, requests):
    pilot = config['pilot']
    found = [r for r in requests if r['history_id'] == pilot['history_id']
             and r['information_cutoff'] == pilot['information_cutoff']]
    if len(found) != 1:
        raise ValueError('Pilot must identify exactly one predeclared request')
    # A deep copy prevents mutating the general request or retained source list.
    request = strict_json(canonical(found[0]))
    request['request_id'] += '__ceo_scope_v06'
    request['extraction_domains'] = ['organizational_role_occupancy']
    request['scope_filter'] = {key: pilot[key] for key in
                              ('scope', 'max_mentions', 'max_resolved_readings_per_mention',
                               'overflow_policy')}
    return request


def summary(request):
    return {'request_id': request['request_id'],
            'history_id': request['history_id'],
            'information_cutoff': request['information_cutoff'],
            'request_sha256': digest(canonical(request)),
            'source_ids': [s['source_id'] for s in request['sources']],
            'source_text_sha256': {s['source_id']: s['text_sha256'] for s in request['sources']},
            'source_context_complete': True,
            'availability_basis': {s['source_id']: s['availability_basis'] for s in request['sources']},
            'source_characters': sum(len(s['text']) for s in request['sources']),
            'scope_filter': request['scope_filter'], 'token_count_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path,
                        default=ROOT / 'data/natural_protocol_v06/request_manifest.json')
    parser.add_argument('--emit-pilot', type=Path)
    args = parser.parse_args()
    config, artifacts, packs, requests = build_requests()
    pilot = pilot_request(config, requests)
    manifest = {'schema_version': 'source_request_manifest_v0.6',
                'builder_path': 'scripts/prepare_natural_protocol_v06.py',
                'builder_sha256': digest(Path(__file__).read_bytes()),
                'config_sha256': digest(CONFIG.read_bytes()),
                'model_executed': False, 'model_scores_collected': False,
                'artifacts': artifacts, 'source_packs': packs,
                'requests': [summary(r) for r in requests], 'pilot': summary(pilot),
                'historical_first_public_availability_verified': False,
                'availability_warning': 'Development operational cutoffs inherited unchanged; not verified historical first availability.',
                'read_scope': 'Pinned source packs, v0.6 prompts/schemas/config and builder code only. No labels, questions or old annotations.'}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_bytes(canonical(manifest) + b'\n')
    if args.emit_pilot:
        args.emit_pilot.parent.mkdir(parents=True, exist_ok=True)
        args.emit_pilot.write_bytes(canonical(pilot) + b'\n')
    print(json.dumps({'requests': len(requests), 'pilot_sources': len(pilot['sources']),
                      'manifest': str(args.manifest), 'pilot_request_sha256': digest(canonical(pilot)),
                      'model_executed': False}))


if __name__ == '__main__':
    main()

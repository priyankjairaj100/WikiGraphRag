#!/usr/bin/env python3
"""Validate exact working inputs, publish compact projections, or replay metadata."""
import argparse
import json
from pathlib import Path
from validate_source_annotation_v08 import validate as annotation_validate, digest
from validate_source_reference_v08 import validate as reference_validate
from publish_source_stream_v08 import compact

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/source_stream_v09'


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def validate_one(working, kind, pid, path):
    req = (working / 'requests' / (pid + '.json')).read_bytes()
    args = [req, path.read_bytes()]
    if kind == 'reference':
        args.append((DATA / 'reference_question_plan.json').read_bytes())
    args.extend((DATA / x).read_bytes() for x in ['capture_manifest.json', 'delivery_manifest.json'])
    return (annotation_validate if kind == 'annotation' else reference_validate)(*args)


def validate_initial(working):
    rows = []
    for req in read(DATA / 'request_manifest.json')['requests']:
        pid = req['prefix_id']
        for kind in ['annotation', 'reference']:
            path = working / (kind + 's') / (pid + '.raw.json')
            row = {'kind': kind, 'prefix_id': pid, 'path': str(path)}
            if not path.exists():
                if kind == 'reference' and pid == 'leadership_disney_2020_2022_p2' and (DATA / 'generation_failure.json').exists():
                    row.update(status='blocked_content_filter_no_artifact', failure_receipt='data/source_stream_v09/generation_failure.json')
                else:
                    row.update(status='not_yet_generated')
            else:
                row.update(raw_output_sha256=digest(path.read_bytes()))
                try:
                    row.update(status='passed_structure_and_spans', validation=validate_one(working, kind, pid, path))
                except (ValueError, KeyError, TypeError, AssertionError) as exc:
                    row.update(status='failed_structure_or_spans', error=str(exc))
            rows.append(row)
    result = {'schema_version': 'source_initial_validation_v0.9', 'artifacts': rows,
              'semantic_correctness_certified': False}
    write(ROOT / 'results/source_initial_validation_v09.json', result)
    print(json.dumps({'counts': {s: sum(x['status'] == s for x in rows) for s in {x['status'] for x in rows}}}))


def publish(working):
    review = read(ROOT / 'results/source_semantic_review_v09.json')
    requests = {r['prefix_id']: r for r in read(DATA / 'request_manifest.json')['requests']}
    entries = []
    assert len(review['artifacts']) == 15
    assert len({(r['kind'], r['prefix_id']) for r in review['artifacts']}) == 15
    assert review['blocked_artifacts'] == [{'kind': 'reference', 'prefix_id': 'leadership_disney_2020_2022_p2', 'receipt': 'data/source_stream_v09/generation_failure.json'}]
    for row in review['artifacts']:
        kind, pid = row['kind'], row['prefix_id']
        raw = working / (kind + 's') / (pid + '.raw.json')
        final = Path(row['final_selected_path'])
        if not final.is_absolute():
            final = working / final
        req = working / 'requests' / (pid + '.json')
        assert digest(req.read_bytes()) == requests[pid]['request_sha256'] == row['request_sha256']
        assert digest(raw.read_bytes()) == row['raw_output_sha256']
        assert digest(final.read_bytes()) == row['final_selected_sha256']
        validate_one(working, kind, pid, final)
        for stage, path in [('initial', raw), ('adjudicated', final)]:
            obj = {'schema_version': 'source_annotation_projection_v0.9', 'kind': kind,
                   'stage': stage, 'prefix_id': pid, 'source_request_sha256': digest(req.read_bytes()),
                   'full_response_sha256': digest(path.read_bytes()),
                   'representation': 'Compact semantic core and span hashes; full text, quotes and prose notes omitted. Not executable decoder inputs.',
                   'content': compact(read(path))}
            dest = DATA / 'distributed' / (kind + 's') / (pid + '.' + stage + '.json')
            write(dest, obj)
            entries.append({'path': str(dest.relative_to(ROOT)), 'sha256': digest(dest.read_bytes()),
                            'kind': kind, 'stage': stage, 'prefix_id': pid})
    paths = [str((DATA / f).relative_to(ROOT)) for f in [
        'capture_selection_plan.json', 'capture_manifest.json', 'content_review.json',
        'delivery_manifest.json', 'request_manifest.json', 'reference_question_plan.json',
        'review_protocol.json', 'generation_manifest.json', 'generation_failure.json']]
    paths += ['results/source_initial_validation_v09.json', 'results/source_semantic_review_v09.json']
    paths += ['configs/source_' + k + '_v08_' + suffix for k in ['annotation', 'reference'] for suffix in ['prompt.txt', 'schema.json']]
    manifest = {'schema_version': 'source_distribution_v0.9', 'files': entries,
                'bindings': [{'path': p, 'sha256': digest((ROOT / p).read_bytes())} for p in paths],
                'limitations': ['Full source texts and quote-bearing requests/responses are outside the package.',
                                'Offline replay verifies metadata integrity, not exact quote occurrence or semantic truth.',
                                'Current sources can change; matching source hashes are required for full revalidation.',
                                'Model-assisted development artifacts; no human gold, benchmark accuracy or historical replay.']}
    write(DATA / 'distribution_manifest.json', manifest)
    replay()


def replay():
    d = read(DATA / 'distribution_manifest.json')
    for row in d['files'] + d['bindings']:
        path = Path(row['path'])
        assert not path.is_absolute() and '..' not in path.parts
        assert digest((ROOT / path).read_bytes()) == row['sha256'], str(path)
    requests = {r['prefix_id']: r for r in read(DATA / 'request_manifest.json')['requests']}
    delivery = read(DATA / 'delivery_manifest.json')
    assert delivery['capture_manifest_sha256'] == digest((DATA / 'capture_manifest.json').read_bytes())
    prefixes = {r['prefix_id']: r for r in delivery['prefixes']}
    assert len(requests) == len(prefixes) == 8
    assert len(d['files']) == 30
    expected = {(kind, stage, pid) for kind in ['annotation', 'reference'] for stage in ['initial', 'adjudicated'] for pid in prefixes
                if (kind, pid) != ('reference', 'leadership_disney_2020_2022_p2')}
    assert {(r['kind'], r['stage'], r['prefix_id']) for r in d['files']} == expected
    claims = questions = spans = 0

    def walk(value, allowed):
        nonlocal spans
        if isinstance(value, list):
            for v in value:
                walk(v, allowed)
        elif isinstance(value, dict):
            assert 'quote' not in value and 'text' not in value
            if 'quote_sha256' in value:
                assert value['source_id'] in allowed
                assert type(value['start']) is int and type(value['end']) is int
                assert 0 <= value['start'] < value['end']
                assert value['end'] - value['start'] == value['quote_characters']
                assert len(value['quote_sha256']) == 64
                spans += 1
            for v in value.values():
                walk(v, allowed)

    for row in d['files']:
        obj = read(ROOT / row['path']); pid = row['prefix_id']; pre = prefixes[pid]
        assert obj['prefix_id'] == pid and obj['kind'] == row['kind'] and obj['stage'] == row['stage']
        assert obj['source_request_sha256'] == requests[pid]['request_sha256']
        assert obj['content']['request_sha256'] == requests[pid]['request_sha256']
        assert obj['content']['eligible_source_ids'] == pre['source_ids'] and pre['status'] == 'ready'
        walk(obj['content'], set(pre['source_ids']))
        if row['stage'] == 'adjudicated':
            if row['kind'] == 'annotation':
                claims += len(obj['content']['claims'])
            else:
                assert len(obj['content']['questions']) == 2
                questions += 2
    out = {'schema_version': 'source_stream_replay_v0.9', 'status': 'passed_distributed_metadata_integrity',
           'new_histories': 4, 'ready_source_prefixes': 8, 'annotation_prefixes': 8,
           'reference_prefixes': 7, 'blocked_reference_prefixes': 1, 'projections_checked': 30,
           'adjudicated_annotation_claims': claims, 'reference_judgments': questions,
           'evidence_hash_entries_checked': spans, 'source_text_revalidated': False,
           'semantic_correctness_certified': False, 'model_predictions_evaluated': 0,
           'new_model_calls': 0, 'distribution_manifest_sha256': digest((DATA / 'distribution_manifest.json').read_bytes())}
    write(ROOT / 'results/source_stream_replay_v09.json', out)
    print(json.dumps(out))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('mode', choices=['validate', 'publish', 'replay'])
    p.add_argument('--working-root', type=Path); args = p.parse_args()
    if args.mode != 'replay' and args.working_root is None:
        p.error('--working-root required for full-text operations')
    if args.mode == 'validate':
        validate_initial(args.working_root)
    elif args.mode == 'publish':
        publish(args.working_root)
    else:
        replay()

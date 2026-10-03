#!/usr/bin/env python3
"""Build neutral source-only ordinal scoring inputs, or validate scorer output.

No model is invoked. The request deliberately omits generator notes, confidence,
unresolved reasons, output order, original IDs and all QA/decoder information.
"""
import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_v06',
                                             ROOT / 'scripts/validate_candidate_graph_v06.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
old = validator.old


def neutral_reading(reading, mention):
    return {'source_id': mention['source_id'], 'anchor_quote': mention['anchor_quote'],
            'interpretation': {k: v for k, v in reading.items()
                               if k not in {'candidate_id', 'derivation_note'}}}


def prepare(source_request, raw):
    validation = validator.validate_graph(raw, source_request)
    graph = old.strict_json(raw)
    readings, mentions = {}, {}
    for mention in graph['mentions']:
        for reading in mention['readings']:
            rid = reading['candidate_id']
            readings[rid] = neutral_reading(reading, mention)
            mentions[rid] = mention
    def neutral_link(link):
        return {'updating_reading': readings[link['candidate_id']],
                'target_reading': readings[link['target_candidate_id']],
                'relation': link['relation'], 'evidence': link['evidence'],
                'correction': link['correction'],
                'declared_authorities': [{'source_id': a['source_id'],
                                          'authority_id': a['authority_id'],
                                          'evidence': a['evidence']}
                                         for a in graph['authorities']
                                         if a['source_id'] in {
                                             mentions[link['candidate_id']]['source_id'],
                                             mentions[link['target_candidate_id']]['source_id']} ]}
    # Paired mapping never enters model content. Hash sorting uses only neutral
    # semantic item content; the opaque item ID is assigned after sorting.
    rows = []
    for mention in graph['mentions']:
        mid = mention['mention_id']
        for reading in mention['readings']:
            rid = reading['candidate_id']
            rows.append(({'kind': 'reading', 'proposition': readings[rid]},
                         {'kind': 'reading', 'mention_id': mid, 'candidate_id': rid, 'link_id': None}))
        alternatives = sorted((readings[r['candidate_id']] for r in mention['readings']),
                              key=lambda row: old.digest(old.canonical(row)))
        rows.append(({'kind': 'unresolved', 'source_id': mention['source_id'],
                      'anchor_quote': mention['anchor_quote'], 'resolved_alternatives': alternatives},
                     {'kind': 'unresolved', 'mention_id': mid, 'candidate_id': None, 'link_id': None}))
        outgoing = [neutral_link(l) for l in graph['links']
                    if mentions[l['candidate_id']]['mention_id'] == mid]
        outgoing.sort(key=lambda row: old.digest(old.canonical(row)))
        rows.append(({'kind': 'no_link', 'source_id': mention['source_id'],
                      'anchor_quote': mention['anchor_quote'], 'outgoing_alternatives': outgoing},
                     {'kind': 'no_link', 'mention_id': mid, 'candidate_id': None, 'link_id': None}))
    for link in graph['links']:
        rows.append(({'kind': 'link', 'proposition': neutral_link(link)},
                     {'kind': 'link', 'mention_id': mentions[link['candidate_id']]['mention_id'],
                      'candidate_id': link['candidate_id'], 'link_id': link['link_id']}))
    rows.sort(key=lambda row: old.digest(old.canonical(row[0])))
    if len({old.digest(old.canonical(row[0])) for row in rows}) != len(rows):
        raise ValueError('Duplicate identical scoring items require explicit candidate review; no silent deduplication')
    items, mapping = [], {}
    for number, (item, target) in enumerate(rows, 1):
        item_id = f's{number:04d}'
        items.append({'item_id': item_id, **item})
        mapping[item_id] = target
    request = {'schema_version': 'ordinal_scoring_request_v0.6',
               'information_cutoff': source_request['information_cutoff'],
               'extraction_domains': source_request['extraction_domains'],
               'scope_filter': source_request['scope_filter'],
               'sources': source_request['sources'], 'items': items}
    manifest = {'schema_version': 'ordinal_scoring_manifest_v0.6',
                'source_request_sha256': old.digest(old.canonical(source_request)),
                'candidate_raw_output_sha256': sha256(raw.encode()).hexdigest(),
                'scoring_request_sha256': old.digest(old.canonical(request)),
                'prompt_sha256': old.digest((ROOT / 'configs/scorer_ordinal_prompt_v06.txt').read_bytes()),
                'schema_sha256': old.digest((ROOT / 'configs/scorer_ordinal_schema_v06.json').read_bytes()),
                'item_mapping': mapping, 'items': len(items),
                'score_definition': 'Fresh-context unversioned model ordinal compatibility integers [-3,3], additive heuristic, unresolved and no-link explicitly judged; beta=1, no calibrated probability interpretation.',
                'context': 'One fresh scorer receives full eligible source context and neutral structural candidates; atomic judgments share this context and are not statistically independent.',
                'model_executed': False,
                'validation': {k: validation[k] for k in ('mentions', 'resolved_readings', 'links')}}
    return request, manifest


def validate_scores(raw, request):
    scores = old.strict_json(raw)
    if set(scores) != {'schema_version', 'scores'} or scores['schema_version'] != 'ordinal_scorer_v0.6':
        raise ValueError('Unexpected ordinal score output fields/version')
    if not isinstance(scores['scores'], list):
        raise ValueError('Scores must be a list')
    expected = {i['item_id'] for i in request['items']}
    if len(expected) != len(request['items']):
        raise ValueError('Duplicate request item ID')
    observed = set()
    sources = {s['source_id']: s for s in request['sources']}
    for row in scores['scores']:
        if not isinstance(row, dict) or set(row) != {'item_id', 'score', 'evidence', 'note'}:
            raise ValueError('Unexpected ordinal score row fields')
        if row['item_id'] not in expected or row['item_id'] in observed:
            raise ValueError('Unknown or duplicate scoring item')
        observed.add(row['item_id'])
        if type(row['score']) is not int or not -3 <= row['score'] <= 3:
            raise ValueError('Scores must be integer ordinal judgments in [-3,3]')
        if not isinstance(row['note'], str) or not row['note'].strip():
            raise ValueError('Scores need a nonempty concise evidence note')
        if not isinstance(row['evidence'], list) or len(row['evidence']) > 8:
            raise ValueError('Evidence must be an array with at most 8 quotes')
        for item in row['evidence']:
            if not isinstance(item, dict) or set(item) != {'source_id', 'quote'}:
                raise ValueError('Unexpected evidence fields')
            if any(not isinstance(v, str) or not v for v in item.values()):
                raise ValueError('Evidence source ID and quote must be nonempty strings')
        old.align_evidence(row['evidence'], sources)
    if observed != expected:
        raise ValueError('Missing scoring items, including unresolved/no-link')
    return {'schema_version': 'ordinal_score_validation_v0.6',
            'scoring_request_sha256': old.digest(old.canonical(request)),
            'raw_score_output_sha256': sha256(raw.encode()).hexdigest(),
            'items': len(expected), 'all_items_judged': True,
            'scores_are_calibrated_probabilities': False,
            'validation_scope': 'Exact item coverage, integer bounds and evidence spans; not entailment or judgment quality.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-request', type=Path)
    parser.add_argument('--candidates', type=Path)
    parser.add_argument('--scoring-request', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--score-output', type=Path)
    parser.add_argument('--validation-report', type=Path)
    args = parser.parse_args()
    if args.score_output:
        if not args.validation_report or args.source_request or args.candidates or args.manifest:
            parser.error('Score validation needs scoring-request, score-output and validation-report only')
        report = validate_scores(args.score_output.read_text(), old.strict_json(args.scoring_request.read_text()))
        args.validation_report.parent.mkdir(parents=True, exist_ok=True)
        args.validation_report.write_bytes(old.canonical(report) + b'\n')
        print(json.dumps(report))
        return
    if not args.source_request or not args.candidates or not args.manifest or args.validation_report:
        parser.error('Preparation needs source-request, candidates, scoring-request and manifest')
    request, manifest = prepare(old.strict_json(args.source_request.read_text()), args.candidates.read_text())
    for path, value in ((args.scoring_request, request), (args.manifest, manifest)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(old.canonical(value) + b'\n')
    print(json.dumps({'items': manifest['items'], 'validation': manifest['validation'],
                      'model_executed': False, 'scoring_request': str(args.scoring_request)}))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Portable replay of released selection/witness metadata, without external filings."""
import json
from pathlib import Path

from evaluate_task_probe_v24 import evaluate, sha, validate_selection
from prepare_reviewed_references_v24 import build
from unpack_retrieval_results_v24 import unpack

ROOT = Path(__file__).resolve().parents[1]


def main():
    unpack()
    def read(p):
        return json.loads((ROOT / p).read_text())
    protocol = read('data/task_probe_v24/protocol.json')
    references = read('results/source_references_reviewed_v24.json')
    if build() != references:
        raise ValueError('source review projection replay differs')
    retrieval = read('results/retrieval_probe_v24.json')
    freeze = read('data/task_probe_v24/retrieval_freeze.json')
    diagnostic = read('results/task_probe_witness_diagnostic_v24.json')
    for path, expected in diagnostic['inputs'].items():
        if sha(ROOT / path) != expected:
            raise ValueError(f'diagnostic input changed: {path}')
    if retrieval['retrieval_freeze_sha256'] != sha(ROOT / 'data/task_probe_v24/retrieval_freeze.json'):
        raise ValueError('retrieval freeze mismatch')
    if retrieval['code_sha256'] != freeze['code_sha256']:
        raise ValueError('retrieval code record differs from freeze')
    for path, expected in freeze['code_sha256'].items():
        if sha(ROOT / path) != expected:
            raise ValueError(f'implementation changed: {path}')
    source_hashes = {s['external_filename']: s['expected_sha256'] for s in protocol['sources']}
    for row in retrieval['records']:
        validate_selection(row, retrieval['block_catalog'], source_hashes)
    replay = evaluate(protocol, references['records'], retrieval, freeze['policies'], freeze['budgets_words'])
    for key, value in replay.items():
        if diagnostic[key] != value:
            raise ValueError(f'diagnostic replay differs: {key}')
    print(json.dumps({'status': 'passed', 'questions': len(references['records']),
                      'retrieval_records': len(retrieval['records']),
                      'source_claims': sum(len(r['claims']) for r in references['records']),
                      'scope': 'metadata/review projection and arithmetic replay; no external source or semantic re-adjudication',
                      'automatic_coverage_authorized': False, 'new_model_predictions': 0}, indent=2))


if __name__ == '__main__':
    main()

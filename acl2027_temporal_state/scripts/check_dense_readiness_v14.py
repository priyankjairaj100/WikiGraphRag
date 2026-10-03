#!/usr/bin/env python3
"""Verify completed native artifacts and emit the encoder readiness record.

This is an experiment sequencing check, not a new user permission request.
It does not launch a model or change an input/output from either experiment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import native_structured_reader_v14 as native

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(external, output):
    if output.exists():
        raise FileExistsError('Preserve the existing readiness record')
    protocol_path = ROOT / 'data/dense_retrieval_v14/source_coverage_protocol.json'
    protocol = json.loads(protocol_path.read_text())
    for spec in protocol['inputs'].values():
        path = ROOT / spec['path']
        if path.stat().st_size != spec['bytes'] or sha(path) != spec['sha256']:
            raise ValueError('Frozen source-coverage input changed: ' + spec['path'])
    for spec in protocol['external_bindings'].values():
        if sha(spec['path']) != spec['sha256']:
            raise ValueError('Frozen external source-coverage input changed')
    receipt_path = ROOT / 'results/native_structured_main_v14_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    if receipt['status'] != 'completed' or receipt['batch_id'] != 'main_v14':
        raise ValueError('The native main batch must finish before encoding')
    counts = ('completion_calls_started', 'raw_responses_returned',
              'validated_responses', 'completion_processes')
    if any(receipt[key] != 32 for key in counts):
        raise ValueError('All 32 native attempts and responses must be accounted for')
    verification = native.verify(external)
    if verification['status'] != 'passed' or verification['validated_responses'] != 32:
        raise ValueError('Native artifact verification failed')
    result = {
        'schema_version': 'dense_encoder_readiness_v14',
        'created_unix_seconds': time.time(),
        'purpose': 'Serialize encoder execution after the verified complete native batch.',
        'allow_encoder_execution': True, 'reader_batch_id': 'main_v14',
        'reader_status': 'completed', 'expected_responses': 32,
        'observed_responses': receipt['validated_responses'],
        'observed_attempts': receipt['completion_calls_started'],
        'retained_reader_output_budget_failures': receipt['output_budget_failures'],
        'native_verification': verification,
        'completed_reader_artifacts': [
            {'path': str(receipt_path), 'role': 'native_reader_receipt',
             'sha256': sha(receipt_path)}],
        'candidate_config_sha256': sha(ROOT / 'configs/dense_retriever_candidate_v14.json'),
        'encoder_script_sha256': sha(ROOT / 'scripts/dense_retrieval_v14.py'),
        'source_coverage_protocol_sha256': sha(protocol_path),
        'readiness_script_sha256': sha(__file__),
        'native_collector_sha256': sha(native.__file__),
        'additional_natural_qa_calls': 0,
        'claim_limit': 'Completion is not semantic correctness or a stronger-reader certificate.',
    }
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    return {'status': 'ready', 'gate_sha256': sha(output),
            'native_verification': verification, 'model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external-main', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.external_main, args.output)))


if __name__ == '__main__':
    main()

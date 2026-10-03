#!/usr/bin/env python3
"""Offline audit of the v0.11 bounded reader attempt, including pre-launch failure.

The retained stopped attempt is auditable evidence about feasibility, never a
reader result. No model load, download, tokenizer, HTTP, or reference access.
"""
import argparse
import json
from pathlib import Path

import native_reader_v11 as runner

PROJECT = Path(__file__).resolve().parents[1]


def check(condition, message):
    if not condition:
        raise ValueError(message)


def audit(external=None):
    frozen_path = runner.DATA / 'frozen_inputs.json'
    frozen = runner.load(frozen_path)
    receipt = runner.load(runner.RECEIPT)
    check(receipt['frozen_inputs_sha256'] == runner.base.digest(frozen_path), 'freeze hash mismatch')
    check(frozen['completion_calls_at_freeze'] == 0, 'freeze occurred after calls')
    for name, sha in frozen['source_hashes'].items():
        path = Path(name) if Path(name).is_absolute() else PROJECT / name
        check(runner.base.digest(path) == sha, 'frozen source changed: ' + name)
    for name, sha in receipt['project_artifacts'].items():
        check(runner.base.digest(PROJECT / name) == sha, 'project artifact changed: ' + name)
    check(set(receipt['project_artifacts']) == {str(p.relative_to(PROJECT))
          for p in runner.DATA.rglob('*') if p.is_file()}, 'project artifact coverage mismatch')
    check(receipt['finished_unix_seconds'] >= receipt['started_unix_seconds'] >=
          frozen['created_unix_seconds'], 'recorded event ordering invalid')
    checked_external = 0
    if external is not None:
        external = runner.external_path(external)
        runner.check_frozen(external)
        for name, spec in receipt['external_artifacts'].items():
            path = external / name
            check(path.stat().st_size == spec['bytes'] and runner.base.digest(path) == spec['sha256'],
                  'external artifact changed: ' + name)
            checked_external += 1
        check(set(receipt['external_artifacts']) == {str(p.relative_to(external))
              for p in external.rglob('*') if p.is_file()}, 'external artifact coverage mismatch')
    if receipt['status'] == 'failed':
        check(receipt['error_type'] == 'RuntimeError', 'unexpected failed-attempt error')
        check(receipt['completion_calls_started'] == receipt['raw_responses_returned'] ==
              receipt['validated_responses'] == receipt['completion_processes'] == 0,
              'this audit only admits the retained pre-launch failure')
        check(receipt['preflight_processes'] == 1 and receipt['all_inputs_preflight_passed'] is False,
              'unexpected preflight accounting')
        trace = runner.load(runner.DATA / 'preflight.execution.json')
        check('process_id' not in trace and trace['cgroup_memory_before_bytes'] is None,
              'native process may have started')
        check(trace['config_sha256'] == frozen['config']['backend_config_sha256'] and
              trace['model_sha256'] == runner.backend.config()['model_sha256'],
              'backend binding differs')
        check(trace['runtime_files_sha256'] == runner.base.canonical_hash(
              runner.backend.config()['runtime_files']), 'runtime file manifest differs')
        check(not any('response' in name or name.endswith('.server.log')
                      for name in receipt['external_artifacts']), 'unexpected model output/log')
        if external is not None:
            check(runner.load(external / 'rendered_prompts.json') == [], 'unexpected tokenized inputs')
            check(runner.load(external / 'failure.json') == {
                'type': 'RuntimeError', 'message': 'required cgroup memory telemetry is unavailable'},
                'failure reason differs')
        status = 'passed_retained_prelaunch_failure'
        conclusion = ('Frozen local attempt stopped because required cgroup telemetry was unavailable. '
                      'Zero model processes launched, zero prompts tokenized, zero completion calls; '
                      'no answer-level result or accuracy is established.')
    elif receipt['status'] == 'completed':
        check(external is not None, 'completed attempt requires full external audit')
        runner.verify(external, runner.backend.DEFAULT_ASSETS)
        status = 'passed_completed_attempt'
        conclusion = 'Native response provenance passes; answer correctness requires separate review.'
    else:
        raise ValueError('unknown or unfinished attempt status')
    return {'schema_version': 'native_reader_attempt_audit_v0.11', 'status': status,
            'mode': 'full_external' if external is not None else 'project_metadata_only',
            'completion_calls': receipt['completion_calls_started'],
            'raw_responses': receipt['raw_responses_returned'],
            'external_artifacts_checked': checked_external,
            'project_artifacts_checked': len(receipt['project_artifacts']),
            'forward_passes_in_audit': 0, 'receipt_sha256': runner.base.digest(runner.RECEIPT),
            'frozen_inputs_sha256': runner.base.digest(frozen_path), 'conclusion': conclusion}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(args.external)
    if args.output:
        runner.base.write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

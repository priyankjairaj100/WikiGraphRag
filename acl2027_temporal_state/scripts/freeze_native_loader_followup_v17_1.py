#!/usr/bin/env python3
"""Prepare one separately approved follow-up after pinned lossless dedup.

No model process, asset rehash, or native API is invoked. The unchanged v17
loader remains the only execution entry point; root must approve the new hash.
"""
import json
from pathlib import Path
import native_loader_v17 as loader

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / 'results/native_loader_attempt_v17.json'
DEDUP = ROOT / 'results/diagnostic_hardlink_dedup_v17.json'
PARENT_SHA = 'b7f279c8752fcac0a575f33d18fa69378ea0a5eb0ec529261cad6ec486ce3314'
DEDUP_SHA = 'e1fd6fa116eb628ad481099f9a5f9a70e5d58ba6a26ac11ed3b120389e75b455'
PREVIOUS = Path('/workspace/scratch/bdef663e3dfc/wikigraph_v17_external/native_loader_attempt01')
DESTINATION = Path('/workspace/scratch/bdef663e3dfc/wikigraph_v17_external/native_loader_attempt02_after_dedup')
PUBLIC = ROOT / 'results/native_loader_attempt_v17_1.json'


def main():
    assert loader.digest(PARENT) == PARENT_SHA and loader.digest(DEDUP) == DEDUP_SHA
    parent, dedup = loader.load(PARENT), loader.load(DEDUP)
    assert parent['status'] == 'failed' and parent['failure_code'] == 'cgroup_pressure_proxy_limit'
    assert parent['model_processes_started'] == parent['tokenized_prompts'] == parent['completion_calls_started'] == 0
    assert parent['cleanup_confirmed']
    assert dedup['status'] == 'completed' and dedup['deduplicated_pairs'] == 6
    assert dedup['preserved_original_paths'] == 12 and dedup['allocated_bytes_saved'] == 60252160
    assert not dedup['lease_break_requests'] and dedup['leases_released'] == 12
    prior = loader.load(PREVIOUS / 'frozen_protocol.json')
    assert loader.digest(PREVIOUS / 'frozen_protocol.json') == parent['protocol_sha256']
    loader.RECIPE = loader.RECIPE + ['scripts/freeze_native_loader_followup_v17_1.py',
          'results/native_loader_attempt_v17.json', 'results/diagnostic_hardlink_dedup_v17.json',
          'results/native_loader_independent_review_v17.json']
    loader.freeze(DESTINATION, PUBLIC)
    frozen = loader.load(DESTINATION / 'frozen_protocol.json')
    assert frozen['native_config'] == prior['native_config'] and frozen['profile'] == prior['profile']
    assert frozen['requests_sha256'] == prior['requests_sha256'] and frozen['request_count'] == 8
    frozen['separate_followup_ancestry'] = {
        'parent_public_receipt': str(PARENT.relative_to(ROOT)), 'parent_failure_sha256': PARENT_SHA,
        'parent_frozen_protocol_sha256': parent['protocol_sha256'],
        'deduplication_receipt': str(DEDUP.relative_to(ROOT)), 'deduplication_receipt_sha256': DEDUP_SHA,
        'reason': 'Lossless, kernel-lease-protected hardlink dedup freed 60252160 tmpfs allocation bytes while preserving all twelve immutable paths and exact contents.',
        'native_driver_unchanged': True, 'model_runtime_profile_caps_and_authored_prompts_unchanged': True,
        'maximum_new_native_processes': 1, 'maximum_new_authored_tokenizations': 8,
        'maximum_new_completions': 0, 'natural_prompts_allowed': False,
        'no_retry_loop': True, 'root_approval_required_for_this_final_protocol_hash': True,
        'new_attempt_not_yet_executed': True}
    loader.write(DESTINATION / 'frozen_protocol.json', frozen)
    public = loader.load(PUBLIC)
    public['protocol_sha256'] = loader.digest(DESTINATION / 'frozen_protocol.json')
    public['parent_failure_sha256'] = PARENT_SHA
    public['deduplication_receipt_sha256'] = DEDUP_SHA
    public['separate_followup_version'] = 'v17.1'
    loader.write(PUBLIC, public)
    print(json.dumps({'status': public['status'], 'protocol_sha256': public['protocol_sha256'],
                      'public_receipt_sha256': loader.digest(PUBLIC), 'model_processes_started': 0}))


if __name__ == '__main__':
    main()

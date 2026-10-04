"""Privileged authored freezer; this entrypoint may read controls/references.

The prediction worker never imports this module or its attestation recipes.
"""
import argparse
import json
from pathlib import Path

import native_completion_worker_v19 as worker

ROOT = worker.ROOT
PARENT = Path('/workspace/scratch/bdef663e3dfc/wikigraph_v18_external/native_loader_attempt02_logging_observation')
REQUEST_ITEMS = ROOT / 'data/reader_binding_v19/authored_requests_v19.json'
REFERENCE_ITEMS = ROOT / 'data/reader_binding_v19/authored_references_v19.json'
OWNER_REVIEW = ROOT / 'results/native_completion_controls_v19.json'
INDEPENDENT_REVIEW = ROOT / 'results/native_completion_independent_review_v19.json'
REVIEW_CODE = worker.WORKER_READ_PINS + (
    'scripts/freeze_native_completion_v19.py', 'scripts/grade_native_completion_v19.py',
    'docs/native_authored_completion_contract_v19.txt',
    'tests/test_native_completion_v19.py', 'tests/test_native_completion_runtime_v19.py',
    'tests/test_native_completion_independent_v19.py',
    'data/reader_binding_v19/authored_requests_v19.json',
    'data/reader_binding_v19/authored_references_v19.json',
    'results/native_completion_controls_v19.json',
)


def reviewed_recipe():
    review = worker.read_json(INDEPENDENT_REVIEW)
    worker.require(review['status'] == 'passed_bounded_pre_freeze_review_root_execution_decision_required' and
                   review['open_blockers'] == [] and review['execution_authorized_by_review'] is False,
                   'independent_review_not_closed')
    current = {name: worker.digest(ROOT / name) for name in REVIEW_CODE}
    worker.require(review['reviewed_sha256'] == current, 'independently_reviewed_recipe_changed')
    return current


def freeze(directory, public):
    p, c = worker.profile()
    directory = worker.fixed_directory(directory, p)
    public = Path(public).resolve()
    worker.require(public == (ROOT / p['public_receipt_path']).resolve(), 'fixed_public_receipt')
    worker.require(not directory.exists() and not public.exists(), 'one_freeze_destination_exists')
    parent_result_path = ROOT / 'results/native_loader_attempt_v18_1.json'
    worker.require(worker.digest(parent_result_path) == p['parent_loader_receipt_sha256'] and
                   worker.digest(ROOT / 'results/native_loader_terminal_review_v18_1.json') == p['parent_loader_terminal_sha256'],
                   'parent_success_receipt_changed')
    parent_result = worker.read_json(parent_result_path)
    parent = worker.read_json(PARENT / 'frozen_protocol.json')
    worker.require(worker.digest(PARENT / 'frozen_protocol.json') == p['parent_loader_protocol_sha256'], 'parent_protocol_changed')
    worker.require(parent_result['status'] == 'loader_tokenizer_passed' and parent_result['cleanup_confirmed'] and
                   parent_result['tokenized_prompts'] == 8 and parent_result['completion_calls_started'] == 0,
                   'parent_tokenizer_gate_not_passed')
    # Freezer only: parent provenance includes combined reference-bearing controls.
    for name, expected in parent['recipe_sha256'].items():
        worker.require(worker.digest(ROOT / name) == expected, 'parent_recipe_changed')
    worker.require(worker.digest(PARENT / 'requests.json') == p['request_messages_sha256'], 'parent_messages_changed')
    items = worker.read_json(REQUEST_ITEMS)
    references = worker.read_json(REFERENCE_ITEMS)
    requests = worker.read_json(PARENT / 'requests.json')
    worker.require(len(items) == len(requests) == 8 and
                   [x['request_id'] for x in items] == p['request_ids'], 'authored_request_count_changed')
    worker.require(requests == [{'request_id': x['request_id'], 'messages': worker.runtime.native.request_messages(x)}
                               for x in items], 'authored_messages_differ_from_parent')
    worker.require(set(references) == {x['fixture_id'] for x in items}, 'authored_reference_join_changed')
    original = worker.read_json(ROOT / 'data/reader_binding_v16/interface_controls_v16.json')
    worker.require(references == original['references'], 'authored_references_differ_from_parent')
    privileged_recipe = reviewed_recipe()
    attested = {'parent_recipe': parent['recipe_sha256'], 'reference_path': str(REFERENCE_ITEMS),
                'reference_sha256': worker.digest(REFERENCE_ITEMS),
                'request_items_path': str(REQUEST_ITEMS), 'request_items_sha256': worker.digest(REQUEST_ITEMS),
                'owner_controls_sha256': worker.digest(OWNER_REVIEW),
                'independent_review_sha256': worker.digest(INDEPENDENT_REVIEW),
                'privileged_recipe_sha256': privileged_recipe}
    (directory / 'inputs').mkdir(parents=True)
    (directory / 'inputs/requests.json').write_bytes((PARENT / 'requests.json').read_bytes())
    for index in range(8):
        source = PARENT / ('native_prompt_%02d.json' % index)
        worker.require(worker.digest(source) == p['parent_native_prompt_sha256'][index], 'parent_prompt_changed')
        target = directory / 'inputs' / ('prompt_%02d.json' % index)
        target.write_bytes(source.read_bytes())
        worker.runtime.native.validate_prompt(worker.read_json(target), c)
    f = {'schema_version': 'native_completion_frozen_v19', 'profile': p, 'native_config': c,
         'public_receipt': str(public), 'worker_read_pins': {name: worker.digest(ROOT / name) for name in worker.WORKER_READ_PINS},
         'input_sha256': {name: worker.digest(directory / 'inputs' / name) for name in worker.INPUT_NAMES},
         'opaque_reference_sha256': worker.digest(REFERENCE_ITEMS),
         'freezer_attestation_sha256': worker.runtime.native.base.canonical_hash(attested),
         'grading_in_worker': False, 'maximum_completions': 8}
    worker.write(directory / 'frozen_protocol.json', f, exclusive=True)
    # Keep this outside the worker directory/read allowlist. The worker never opens it.
    worker.write(directory.parent / (directory.name + '_freezer_attestation.json'), attested, exclusive=True)
    r = {'schema_version': 'native_authored_completion_receipt_v19',
         'status': 'frozen_awaiting_root_execution_approval',
         'protocol_sha256': worker.digest(directory / 'frozen_protocol.json'),
         'expected_completions': 8, 'model_processes_started': 0,
         'completion_calls_started': 0, 'completion_responses': 0, 'natural_prompts': 0,
         'grading_performed': False, 'raw_outputs_sealed': False,
         'request_states': [{'request_id': name, 'status': 'pending', 'process_started': False,
                            'completion_started': False, 'response_received': False} for name in p['request_ids']]}
    worker.write(public, r, exclusive=True)
    return r


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.external, args.receipt)))

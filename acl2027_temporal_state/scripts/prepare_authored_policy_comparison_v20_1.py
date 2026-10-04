"""Prepare a development comparison, without tokens, references or model calls.

This is not a native execution protocol or authorization. The original authored
fixtures remain frozen. Both new conditions use the same neutral pack IDs.
"""
from copy import deepcopy
from hashlib import sha256
import argparse
import json
from pathlib import Path

import native_binding_reader_v16 as native

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / 'data/reader_binding_v19/authored_requests_v19.json'
PARENT_SHA = '7122f2bf5533664ebf713c5a352d19db8353af69d91a619d91d47708b29cabce'
POLICY = '''Decision policy clarification:
Treat explicitly requested source versions as one jointly requested set. Give
each requested quantity separately; differing values across requested versions
are not a reason to refuse. In B1 put the requested claims in one hypothesis.
If an unspecified, answer-changing source choice remains, ask for source
clarification. Do not substitute an unsolicited list of all alternatives.
A required null binding or absent supporting witness means insufficient
evidence. Words in the question cannot supply a missing evidence binding.
Return a schema-valid empty refusal when the evidence is insufficient.
The unknown flag means unresolved interpretation: true when an unspecified
choice remains, false when the request is resolved, including a well-specified
request with insufficient evidence. clarify_aspects names the unresolved
aspects on clarification and is empty otherwise.'''


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def prepare(parent_bytes):
    if sha256(parent_bytes).hexdigest() != PARENT_SHA:
        raise ValueError('frozen_authored_input_changed')
    parents = json.loads(parent_bytes)
    if len(parents) != 8:
        raise ValueError('eight_parent_requests_required')
    neutral = {}
    messages, mapping = [], []
    for index, original in enumerate(parents):
        item = deepcopy(original)
        old_id = item['pack']['pack_id']
        if old_id not in neutral:
            neutral[old_id] = f'p{len(neutral) + 1:04d}'
        item['pack']['pack_id'] = neutral[old_id]
        base = native.request_messages(item)
        # Balance first condition within each arm as well as overall.
        order = ('original_policy', 'clarified_policy')
        if (index // 2 + index) % 2:
            order = tuple(reversed(order))
        for condition in order:
            request_id = f'r{len(messages) + 1:04d}'
            pair = deepcopy(base)
            if condition == 'clarified_policy':
                pair[0]['content'] += '\n\n' + POLICY
            messages.append({'request_id': request_id, 'messages': pair})
            mapping.append({'request_id': request_id,
                            'parent_request_id': original['request_id'],
                            'condition': condition, 'arm': original['arm'],
                            'neutral_pack_id': neutral[old_id]})
    return messages, mapping


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    requests, mapping = prepare(PARENT.read_bytes())
    args.output_directory.mkdir(parents=True, exist_ok=False)
    outputs = {'requests.json': requests, 'administrative_mapping.json': mapping}
    files = []
    for name, value in outputs.items():
        raw = encoded(value)
        (args.output_directory / name).write_bytes(raw)
        files.append({'path': name, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()})
    manifest = {'schema_version': 'authored_policy_preparation_v20_1',
                'status': 'prepared_inputs_not_native_execution_protocol',
                'parent_request_sha256': PARENT_SHA,
                'builder_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
                'legacy_prompt_writer_sha256': sha256(Path(native.__file__).read_bytes()).hexdigest(),
                'files': files, 'planned_development_requests': len(requests),
                'native_calls': 0, 'tokenizations': 0, 'natural_questions': 0,
                'supersedes_unexecuted_preparation': 'data/authored_policy_comparison_v20/manifest.json',
                'amendment': 'Counterbalance first condition within each arm; initial order was confounded with arm.',
                'execution_authorized_by_this_artifact': False,
                'held_out': False, 'actual_input_token_fit': 'unverified',
                'required_before_execution': ['independent_input_review',
                    'complete_native_protocol_and_resource_freeze',
                    'exact_model_and_runtime_assets',
                    'durable_per_completion_capture_integration_and_readback',
                    'separate_reference_binding_and_post_seal_grader']}
    (args.output_directory / 'manifest.json').write_bytes(encoded(manifest))
    print(json.dumps({'requests': len(requests), 'native_calls': 0, 'status': manifest['status']}))


if __name__ == '__main__':
    main()

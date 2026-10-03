#!/usr/bin/env python3
"""Reproduce the pre-main32-request native token binding check; no completions."""
import argparse
import json
from pathlib import Path
import time
import native_structured_reader_v14 as n


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--requests', type=Path, required=True)
    p.add_argument('--retrieval', type=Path, required=True)
    p.add_argument('--external', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert not a.output.exists(), 'Preserve previous token-binding records'
    c = n.config(a.config)
    requests = n.request_items(a.requests, 32)
    report = json.loads(a.retrieval.read_text())
    rows = {x['question_id'] + '__' + x['condition']: x for x in report['records']}
    result = {'schema_version': 'prepared_request_native_token_binding_v14',
        'completion_calls': 0, 'status': 'started', 'created_unix_seconds': time.time(),
        'request_sha256': n.base.digest(a.requests), 'retrieval_sha256': n.base.digest(a.retrieval),
        'config_sha256': n.base.digest(a.config), 'records': []}
    with n.tokenizer_session(a.config, a.external) as counter:
        for item in requests:
            prompt = n.base.render_and_tokenize(counter.port, n.messages(item, c))
            n.validate_prompt(prompt, c)
            expected = rows[item['request_id']]['native_tokens']
            assert prompt['input_tokens'] == expected['input_tokens']
            assert prompt['input_token_ids_sha256'] == expected['token_ids_sha256']
            assert prompt['rendered_prompt_sha256'] == expected['rendered_prompt_sha256']
            result['records'].append({'request_id': item['request_id'], **n.project_prompt(prompt)})
    result.update(status='passed', finished_unix_seconds=time.time())
    n.write(a.output, result)
    print(json.dumps({'status': 'passed', 'native_input_bindings': len(result['records']),
                      'completion_calls': 0}))


if __name__ == '__main__':
    main()

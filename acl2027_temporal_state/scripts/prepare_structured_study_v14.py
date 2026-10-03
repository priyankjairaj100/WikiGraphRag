#!/usr/bin/env python3
"""Prepare frozen requests or condition-masked reviews; no prediction or repair."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ('ordinary', 'paired', 'parent', 'closure')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def contexts(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    indexed = {(r['question_id'], r['condition']): r for r in rows}
    qs = read(ROOT / 'data/paired_reader_v13/questions.json')['questions']
    assert len(rows) == len(indexed) == 32
    assert set(indexed) == {(q['question_id'], c) for q in qs for c in CONDITIONS}
    return qs, indexed


def requests(a):
    from native_structured_reader_v14 import build_prompt
    qs, indexed = contexts(a.contexts)
    items, records = [], []
    for i, q in enumerate(qs):
        order = CONDITIONS[i % 4:] + CONDITIONS[:i % 4]
        for c in order:
            ctx = indexed[q['question_id'], c]
            prompt = build_prompt(q['question'], ctx['context'])
            rid = q['question_id'] + '__' + c
            items.append({'request_id': rid, 'prompt': prompt})
            records.append({'request_id': rid, 'question_id': q['question_id'],
                            'condition': c, 'history_id': q['history_id'],
                            'context_sha256': hashlib.sha256(ctx['context'].encode()).hexdigest(),
                            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
    assert not a.output.resolve().is_relative_to(ROOT.parent)
    write_new(a.output, items)
    report = {'schema_version': 'structured_requests_v14', 'reference_labels_loaded': False,
              'contexts_sha256': sha(a.contexts), 'requests_sha256': sha(a.output),
              'questions_sha256': sha(ROOT / 'data/paired_reader_v13/questions.json'),
              'builder_sha256': sha(__file__), 'records': records}
    write_new(a.manifest, report)
    print(json.dumps({'requests': len(items), 'requests_sha256': report['requests_sha256']}))


def grading(a):
    qs, indexed = contexts(a.contexts)
    qmap = {q['question_id']: q for q in qs}
    domain = a.domain
    reference_path = ROOT / f'data/paired_reader_v13/references_{domain}.json'
    refs = {r['question_id']: r for r in read(reference_path)['references']}
    frozen = read(a.native / 'frozen_inputs.json')
    public = Path(frozen['data_dir'])
    cases, records = [], []
    for i, rid in enumerate(frozen['execution_order']):
        qid, cond = rid.rsplit('__', 1)
        if qid not in refs:
            continue
        ctx = indexed[qid, cond]['context']
        response_path = a.native / f'process_{i:02d}.response.json'
        projection_path = public / f'process_{i:02d}.response_projection.json'
        response, projection = read(response_path), read(projection_path)
        answer = response['content']
        answer_sha = hashlib.sha256(answer.encode()).hexdigest()
        assert projection['content_sha256'] == answer_sha
        cid = 'case_' + hashlib.sha256(('v14-mask:' + rid).encode()).hexdigest()[:12]
        context_sha = hashlib.sha256(ctx.encode()).hexdigest()
        cases.append({'case_id': cid, 'question_id': qid, 'question': qmap[qid]['question'],
                      'reference': refs[qid], 'received_context': ctx, 'raw_answer': answer,
                      'native_output_budget_failure': response['stop_type'] == 'limit',
                      'content_sha256': answer_sha, 'context_sha256': context_sha})
        records.append({'case_id': cid, 'request_id': rid, 'question_id': qid,
                        'condition': cond, 'content_sha256': answer_sha, 'context_sha256': context_sha,
                        'native_response_file_sha256': sha(response_path),
                        'response_projection_sha256': sha(projection_path)})
    assert len(cases) == 16
    cases.sort(key=lambda r: r['case_id'])
    records.sort(key=lambda r: r['case_id'])
    contract_path = ROOT / 'data/paired_reader_v13/scoring_contract.json'
    packet = {'schema_version': 'masked_structured_reader_grading_v14',
              'instructions': 'Grade exact answers and actual cited context against frozen references. Do not inspect mapping manifests, condition labels or other outputs. Model-assisted grading with shared development familiarity; not independent human gold. Preserve all outputs. Explicit rounding is required where the frozen reference says so; an unqualified approximate amount is ambiguous.',
              'scoring_contract': read(contract_path), 'cases': cases}
    assert not a.output.resolve().is_relative_to(ROOT.parent)
    write_new(a.output, packet)
    report = {'schema_version': 'structured_grading_manifest_v14', 'domain': domain,
              'packet_sha256': sha(a.output), 'reference_sha256': sha(reference_path),
              'scoring_contract_sha256': sha(contract_path), 'contexts_sha256': sha(a.contexts),
              'script_sha256': sha(__file__), 'condition_labels_masked_in_packet': True,
              'statistical_blindness_claimed': False, 'records': records}
    write_new(a.manifest, report)
    print(json.dumps({'cases': len(cases), 'packet_sha256': report['packet_sha256']}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['requests', 'grading'])
    p.add_argument('--contexts', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--native', type=Path)
    p.add_argument('--domain', choices=['plug', 'opera'])
    a = p.parse_args()
    if a.mode == 'requests':
        requests(a)
    else:
        assert a.native is not None and a.domain is not None
        grading(a)


if __name__ == '__main__':
    main()

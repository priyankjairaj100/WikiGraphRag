"""Prepare condition-masked grading packets from immutable returned responses."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path('/workspace/scratch/bdef663e3dfc/tmp/paired_reader_v13')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(batch, domain):
    assert batch in ('main', 'layout') and domain in ('plug', 'opera')
    history = 'plug_power_2018_restatement' if domain == 'plug' else 'opera_1109_4897'
    contexts_path = EXTERNAL / ('contexts.jsonl' if batch == 'main' else 'layout_contexts.jsonl')
    contexts = {(r['question_id'], r['condition']): r for r in
                map(json.loads, contexts_path.read_text().splitlines())}
    questions_path = ROOT / 'data/paired_reader_v13/questions.json'
    questions = {q['question_id']: q for q in json.loads(questions_path.read_text())['questions']}
    reference_path = ROOT / f'data/paired_reader_v13/references_{domain}.json'
    references = {r['question_id']: r for r in json.loads(reference_path.read_text())['references']}
    native = EXTERNAL / f'{batch}_attempt01'
    frozen = json.loads((native / 'frozen_inputs.json').read_text())
    public_data = Path(frozen['data_dir'])
    selected = []
    for i, request_id in enumerate(frozen['execution_order']):
        question_id, condition = request_id.rsplit('__', 1)
        if questions[question_id]['history_id'] != history:
            continue
        response_path = native / f'process_{i:02}.response.json'
        projection_path = public_data / f'process_{i:02}.response_projection.json'
        response = json.loads(response_path.read_text())
        projection = json.loads(projection_path.read_text())
        answer = response['content']
        assert projection['content_sha256'] == hashlib.sha256(answer.encode()).hexdigest()
        context = contexts[(question_id, condition)]
        case_id = 'case_' + hashlib.sha256(('v13-mask:' + batch + ':' + request_id).encode()).hexdigest()[:12]
        selected.append((case_id, request_id, question_id, response_path, projection_path,
                         {'case_id': case_id, 'question_id': question_id,
                          'question': questions[question_id]['question'],
                          'reference': references[question_id], 'received_context': context['context'],
                          'raw_answer': answer, 'native_output_budget_failure': response['stop_type'] == 'limit',
                          'content_sha256': projection['content_sha256'],
                          'context_sha256': hashlib.sha256(context['context'].encode()).hexdigest()}))
    assert len(selected) == 8
    selected.sort(key=lambda row: row[0])
    output = EXTERNAL / 'grading' / f'{batch}_{domain}.json'
    metadata = ROOT / 'data/paired_reader_v13' / f'grading_{batch}_{domain}_manifest.json'
    assert not output.exists() and not metadata.exists()
    output.parent.mkdir(exist_ok=True)
    contract_path = ROOT / 'data/paired_reader_v13/scoring_contract.json'
    packet = {'schema_version': 'masked_reader_grading_packet_v0.13',
              'instructions': 'Grade only this packet and the frozen scoring contract. Do not inspect case-mapping metadata, native response directories or retrieval labels. Shared development familiarity and source authorship are disclosed; masking labels is not a claim of statistical blindness. Preserve every raw answer; give no output repair.',
              'scoring_contract': json.loads(contract_path.read_text()),
              'cases': [row[-1] for row in selected]}
    output.write_text(json.dumps(packet, indent=2) + '\n')
    receipt_path = Path(frozen['receipt_path'])
    native_status = json.loads(receipt_path.read_text())['status']
    manifest = {'schema_version': 'reader_grading_packet_manifest_v0.13', 'batch': batch, 'domain': domain,
                'packet_sha256': digest(output), 'reference_sha256': digest(reference_path),
                'questions_sha256': digest(questions_path), 'scoring_contract_sha256': digest(contract_path),
                'contexts_sha256': digest(contexts_path), 'script_sha256': digest(Path(__file__)),
                'native_batch_status_at_packet_creation': native_status,
                'condition_labels_masked_in_packet': True, 'statistical_blindness_claimed': False,
                'records': [{'case_id': cid, 'request_id': rid, 'question_id': qid,
                             'native_response_file_sha256': digest(resp),
                             'response_projection_sha256': digest(proj),
                             'content_sha256': case['content_sha256'], 'context_sha256': case['context_sha256']}
                            for cid, rid, qid, resp, proj, case in selected]}
    metadata.write_text(json.dumps(manifest, indent=2) + '\n')
    return {'case_count': 8, 'packet': str(output), 'packet_sha256': manifest['packet_sha256'],
            'native_batch_status': native_status}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', choices=['main', 'layout'], required=True)
    parser.add_argument('--domain', choices=['plug', 'opera'], required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.batch, args.domain)))

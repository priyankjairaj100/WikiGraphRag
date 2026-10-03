"""Join fixed questions with retrieved contexts; never read answer references."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(questions_path, contexts_path, output_path, manifest_path):
    questions = json.loads(questions_path.read_text())['questions']
    assert len(questions) == 8
    assert len({q['question_id'] for q in questions}) == 8
    assert all(set(q) == {'question_id', 'history_id', 'question'} for q in questions)
    contexts = [json.loads(line) for line in contexts_path.read_text().splitlines() if line.strip()]
    indexed = {(c['question_id'], c['condition']): c for c in contexts}
    expected = {(q['question_id'], c) for q in questions for c in ('ordinary', 'paired')}
    assert len(contexts) == len(indexed) == 16 and set(indexed) == expected
    assert not output_path.resolve().is_relative_to(ROOT.parent)
    assert not output_path.exists() and not manifest_path.exists()
    requests, rows = [], []
    for index, question in enumerate(questions):
        # Alternate which condition executes first; both render source chunks
        # in the same chronological order. No performance-based scheduling.
        conditions = ('ordinary', 'paired') if index % 2 == 0 else ('paired', 'ordinary')
        for condition in conditions:
            context = indexed[(question['question_id'], condition)]
            prompt = ('Question:\n' + question['question'] + '\n\nSource excerpts:\n' +
                      context['context'] + '\n\nAnswer the question using the supplied excerpts and cite source pages.')
            request_id = question['question_id'] + '__' + condition
            requests.append({'request_id': request_id, 'prompt': prompt})
            rows.append({'request_id': request_id, 'question_id': question['question_id'],
                         'history_id': question['history_id'], 'condition': condition,
                         'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                         'context_sha256': hashlib.sha256(context['context'].encode()).hexdigest(),
                         'prompt_words': len(prompt.split())})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(requests, indent=2) + '\n')
    report = {'schema_version': 'paired_reader_request_manifest_v0.13',
              'question_file_sha256': digest(questions_path), 'contexts_file_sha256': digest(contexts_path),
              'request_file_sha256': digest(output_path), 'request_bytes': output_path.stat().st_size,
              'builder_sha256': digest(Path(__file__)), 'requests': rows,
              'model_calls': 0, 'reference_labels_loaded': False,
              'full_source_or_prompt_text_distributed': False,
              'native_token_preflight': 'Required before prediction; not inferred from word counts.'}
    manifest_path.write_text(json.dumps(report, indent=2) + '\n')
    return {'requests': len(requests), 'request_sha256': report['request_file_sha256'],
            'maximum_prompt_words': max(r['prompt_words'] for r in rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions', type=Path, required=True)
    parser.add_argument('--contexts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    a = parser.parse_args()
    print(json.dumps(prepare(a.questions, a.contexts, a.output, a.manifest)))

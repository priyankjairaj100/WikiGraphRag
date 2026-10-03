#!/usr/bin/env python3
"""Compare fixed BM25 top-six with three-per-version evidence selection.

Reads only question_id, history_id and question. Never reads answer references.
Full contexts are external; released metadata contains hashes/locators/scores.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
K1, B = 1.2, 0.75


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tokens(text: str) -> list[str]:
    return re.findall(r'[^\W_]+', text.lower(), flags=re.UNICODE)


def rank_bm25(chunks: list[dict], question: str) -> list[tuple[float, dict]]:
    if not chunks:
        raise ValueError('empty history corpus')
    counts = [Counter(tokens(chunk['text'])) for chunk in chunks]
    lengths = [sum(counter.values()) for counter in counts]
    average = sum(lengths) / len(lengths)
    if not average:
        raise ValueError('empty token corpus')
    frequencies = Counter(term for counter in counts for term in counter)
    terms = sorted(set(tokens(question)))
    scores = []
    for chunk, counter, length in zip(chunks, counts, lengths):
        score = 0.0
        for term in terms:
            frequency = counter[term]
            if not frequency:
                continue
            inverse = math.log(1 + (len(chunks) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            denominator = frequency + K1 * (1 - B + B * length / average)
            score += inverse * frequency * (K1 + 1) / denominator
        scores.append((score, chunk))
    return sorted(scores, key=lambda pair: (-pair[0], pair[1]['chunk_id']))


def select(ranked: list[tuple[float, dict]], condition: str) -> list[tuple[float, dict]]:
    if condition == 'ordinary':
        selected = ranked[:6]
    elif condition == 'paired':
        documents = sorted({chunk['document_id'] for _, chunk in ranked})
        if len(documents) != 2:
            raise ValueError('paired history must contain exactly two documents')
        selected = [pair for doc in documents for pair in [p for p in ranked if p[1]['document_id'] == doc][:3]]
    else:
        raise ValueError('unknown condition')
    if len(selected) != 6:
        raise ValueError('insufficient chunks for the fixed six-chunk budget')
    return sorted(selected, key=lambda p: (p[1]['version_order'], p[1]['document_id'], p[1]['pdf_page'], p[1]['chunk_index']))


def render(selected: list[tuple[float, dict]]) -> str:
    return '\n\n'.join(
        f"[{c['document_id']} p.{c['pdf_page']} | chunk={c['chunk_id']} | version={c['version_order']}]\n{c['text']}"
        for _, c in selected
    )


def load_corpus(manifest_path: Path, chunks_path: Path | None = None) -> tuple[dict, list[dict]]:
    manifest = json.loads(manifest_path.read_text())
    chunks_path = chunks_path or Path(manifest['external_corpus_root']) / manifest['chunks_file']
    raw = chunks_path.read_bytes()
    if sha(raw) != manifest['chunks_sha256']:
        raise ValueError('corpus hash mismatch')
    chunks = [json.loads(line) for line in raw.decode().splitlines()]
    if len(chunks) != manifest['chunk_count'] or len({c['chunk_id'] for c in chunks}) != len(chunks):
        raise ValueError('corpus count or uniqueness mismatch')
    for chunk in chunks:
        if sha(chunk['text'].encode()) != chunk['text_sha256'] or len(chunk['text'].split()) != chunk['word_count']:
            raise ValueError('chunk content metadata mismatch')
        if chunk['word_count'] > 160:
            raise ValueError('chunk exceeds fixed word cap')
    return manifest, chunks


def questions_only(path: Path) -> list[dict]:
    value = json.loads(path.read_text())
    rows = value if isinstance(value, list) else value['questions']
    questions = [{key:row[key] for key in ('question_id', 'history_id', 'question')} for row in rows]
    if not questions or len({q['question_id'] for q in questions}) != len(questions):
        raise ValueError('empty or duplicate question IDs')
    for q in questions:
        if not all(isinstance(value, str) and value.strip() for value in q.values()):
            raise ValueError('questions require nonempty string fields')
    return questions


def retrieve(questions_path: Path, manifest_path: Path, external_output: Path, metadata_output: Path, chunks_path: Path | None = None) -> dict:
    if external_output.resolve().is_relative_to(ROOT.parent):
        raise ValueError('full contexts must stay outside the repository')
    if external_output.exists() or metadata_output.exists():
        raise FileExistsError('refusing to overwrite frozen retrieval outputs')
    manifest, chunks = load_corpus(manifest_path, chunks_path)
    questions = questions_only(questions_path)
    contexts, metadata = [], []
    for question in questions:
        pool = [c for c in chunks if c['history_id'] == question['history_id']]
        ranked = rank_bm25(pool, question['question'])
        rank_by_id = {c['chunk_id']:i for i,(_,c) in enumerate(ranked,1)}
        for condition in ('ordinary', 'paired'):
            selected = select(ranked, condition)
            context = render(selected)
            evidence_words = sum(chunk['word_count'] for _,chunk in selected)
            if evidence_words > 960:
                raise ValueError('evidence exceeds frozen word budget')
            row = {'question_id': question['question_id'], 'history_id': question['history_id'],
                   'condition': condition, 'query_sha256': sha(question['question'].encode()),
                   'context_sha256': sha(context.encode()), 'context_word_count_including_headers': len(context.split()),
                   'evidence_word_count': evidence_words, 'chunk_count': len(selected),
                   'history_pool_chunk_count': len(pool), 'selected_chunk_ids': [c['chunk_id'] for _,c in selected],
                   'selected': [{'chunk_id': c['chunk_id'], 'document_id': c['document_id'], 'version_order': c['version_order'],
                                 'pdf_page': c['pdf_page'], 'chunk_index': c['chunk_index'], 'bm25_score': score,
                                 'history_rank': rank_by_id[c['chunk_id']], 'word_count': c['word_count'],
                                 'text_sha256': c['text_sha256'], 'provenance': c['provenance']} for score,c in selected]}
            metadata.append(row)
            contexts.append({**row, 'question': question['question'], 'context': context})
    external_output.parent.mkdir(parents=True, exist_ok=True)
    with external_output.open('x') as output:
        for row in contexts:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    result = {'schema_version':'paired_retrieval_v0.13', 'script_sha256':sha(Path(__file__).read_bytes()),
              'question_file_sha256': sha(questions_path.read_bytes()), 'corpus_manifest_sha256':sha(manifest_path.read_bytes()),
              'corpus_chunks_sha256': manifest['chunks_sha256'], 'external_contexts_path':str(external_output),
              'external_contexts_sha256':sha(external_output.read_bytes()),
              'algorithm': {'name':'BM25', 'k1':K1, 'b':B, 'tokenization':'Unicode lowercase alphanumeric, underscores excluded',
                            'query_terms':'unique sorted terms, no query expansion', 'idf':'log(1+(N-df+0.5)/(df+0.5))',
                            'statistics_pool':'both documents within the question history', 'tie_break':'ascending chunk_id',
                            'ordinary':'top 6 within history', 'paired':'top 3 per document under same history scores',
                            'context_order':'version_order,document_id,pdf_page,chunk_index',
                            'fulltext_oracle_access':False,'reference_fields_used':False,'post_retrieval_truncation':False},
              'question_count':len(questions), 'context_count':len(contexts), 'records':metadata}
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,default=ROOT/'data/paired_reader_v13/corpus_manifest.json')
    parser.add_argument('--chunks',type=Path)
    parser.add_argument('--contexts',type=Path,default=Path('/workspace/scratch/bdef663e3dfc/tmp/paired_reader_v13/contexts.jsonl'))
    parser.add_argument('--metadata',type=Path,default=ROOT/'results/paired_retrieval_v13.json')
    args=parser.parse_args()
    result=retrieve(args.questions,args.manifest,args.contexts,args.metadata,args.chunks)
    print(json.dumps({k:result[k] for k in ('question_count','context_count','external_contexts_sha256')}))


if __name__ == '__main__':
    main()

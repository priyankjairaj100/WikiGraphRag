#!/usr/bin/env python3
"""Restore source-native whitespace inside frozen selected word spans only.

No search, references, model responses, inference, answer-specific selection,
additional source words, inferred table headers or inferred units are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / 'data/paired_reader_v13/layout_protocol.json'
DEFAULT_CONTEXTS = Path('/workspace/scratch/bdef663e3dfc/tmp/paired_reader_v13/layout_contexts.jsonl')
DEFAULT_METADATA = ROOT / 'results/layout_contexts_v13.json'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_bytes(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    if sha(raw) != expected:
        raise ValueError(f'input hash mismatch: {path}')
    return raw


def restore_span(page: str, word_start: int, word_stop: int, original: str) -> str:
    """Keep exactly selected words and every original whitespace between them."""
    matches = list(re.finditer(r'\S+', page))
    if not 0 <= word_start < word_stop <= len(matches):
        raise ValueError('invalid source word span')
    restored = page[matches[word_start].start():matches[word_stop-1].end()]
    if restored.split() != original.split():
        raise ValueError('source words differ from frozen chunk')
    if len(restored.split()) != word_stop-word_start:
        raise ValueError('span word count mismatch')
    return restored


def header(chunk: dict) -> str:
    return f"[{chunk['document_id']} p.{chunk['pdf_page']} | chunk={chunk['chunk_id']} | version={chunk['version_order']}]"


def render_chunks(chunks: list[dict], text_by_id: dict[str, str]) -> str:
    return '\n\n'.join(header(chunk)+'\n'+text_by_id[chunk['chunk_id']] for chunk in chunks)


def run(protocol_path: Path, output: Path, metadata_output: Path) -> dict:
    if output.resolve().is_relative_to(ROOT.parent):
        raise ValueError('full source contexts must stay outside the repository')
    if output.exists() or metadata_output.exists():
        raise FileExistsError('refusing to overwrite layout follow-up outputs')
    protocol = json.loads(protocol_path.read_text())
    if protocol['schema_version'] != 'layout_followup_protocol_v0.13':
        raise ValueError('unsupported protocol')
    inputs = protocol['inputs']
    originals = {}
    for key, record in inputs.items():
        path = Path(record['path'])
        if not path.is_absolute():
            path = ROOT / path
        originals[key] = checked_bytes(path, record['sha256'])
    manifest = json.loads(originals['corpus_manifest'])
    retrieval = json.loads(originals['retrieval_metadata'])
    contexts = [json.loads(line) for line in originals['external_original_contexts'].decode().splitlines()]
    chunks = [json.loads(line) for line in originals['external_corpus_chunks'].decode().splitlines()]
    by_id = {c['chunk_id']: c for c in chunks}
    if len(by_id) != len(chunks):
        raise ValueError('duplicate corpus chunk IDs')
    by_cell = {(r['question_id'],r['condition']):r for r in retrieval['records']}
    if len(by_cell) != len(retrieval['records']) or len(contexts) != protocol['algorithm']['output_contexts']:
        raise ValueError('context count or uniqueness mismatch')
    pages = {(d['document_id'],p['pdf_page']):p for d in manifest['documents'] for p in d['pages']}
    page_cache, restored_by_id, span_receipts = {}, {}, []
    new_contexts, records, seen_cells = [], [], set()
    for row in contexts:
        cell = (row['question_id'],row['condition'])
        if cell in seen_cells or cell not in by_cell:
            raise ValueError('unexpected or duplicate context cell')
        seen_cells.add(cell)
        public_original = by_cell[cell]
        for key,value in public_original.items():
            if row.get(key) != value:
                raise ValueError(f'original context metadata mismatch: {cell}, {key}')
        if sha(row['question'].encode()) != row['query_sha256'] or sha(row['context'].encode()) != row['context_sha256']:
            raise ValueError('original query/context hash mismatch')
        selected = [by_id[chunk_id] for chunk_id in row['selected_chunk_ids']]
        if [entry['chunk_id'] for entry in row['selected']] != row['selected_chunk_ids']:
            raise ValueError('selected score records/order differ')
        normalized = {c['chunk_id']:c['text'] for c in selected}
        if render_chunks(selected,normalized) != row['context']:
            raise ValueError('original context cannot be reconstructed exactly')
        for chunk in selected:
            chunk_id = chunk['chunk_id']
            if chunk_id in restored_by_id:
                continue
            key = (chunk['document_id'],chunk['pdf_page'])
            page_record = pages[key]
            if key not in page_cache:
                page_path = Path(manifest['external_corpus_root'])/chunk['document_id']/f"p{chunk['pdf_page']:03}.txt"
                page_cache[key] = checked_bytes(page_path,page_record['corpus_page_sha256']).decode('utf-8')
            if sha(chunk['text'].encode()) != chunk['text_sha256']:
                raise ValueError('original chunk text hash mismatch')
            restored = restore_span(page_cache[key],chunk['word_start_0based'],chunk['word_stop_exclusive'],chunk['text'])
            if len(restored.split()) != chunk['word_count']:
                raise ValueError('chunk word count differs')
            restored_by_id[chunk_id] = restored
            span_receipts.append({'chunk_id':chunk_id,'document_id':chunk['document_id'],'pdf_page':chunk['pdf_page'],
                                  'word_start_0based':chunk['word_start_0based'],'word_stop_exclusive':chunk['word_stop_exclusive'],
                                  'source_page_sha256':page_record['corpus_page_sha256'],
                                  'normalized_chunk_sha256':chunk['text_sha256'],'layout_chunk_sha256':sha(restored.encode()),
                                  'word_count':chunk['word_count'],'word_sequence_unchanged':True})
        layout_context = render_chunks(selected,restored_by_id)
        evidence_words = sum(len(restored_by_id[c['chunk_id']].split()) for c in selected)
        if evidence_words != row['evidence_word_count'] or evidence_words > 960:
            raise ValueError('evidence budget/invariance failure')
        if layout_context.split() != row['context'].split() or len(layout_context.split()) != row['context_word_count_including_headers']:
            raise ValueError('whole-context word sequence changed')
        layout_hash = sha(layout_context.encode())
        new_contexts.append({**row,'context':layout_context,'context_sha256':layout_hash,
                             'original_context_sha256':row['context_sha256'],'rendering':'source_native_internal_whitespace',
                             'layout_protocol_sha256':sha(protocol_path.read_bytes())})
        records.append({**public_original,'context_sha256':layout_hash,'original_context_sha256':row['context_sha256'],
                        'rendering':'source_native_internal_whitespace','whole_context_word_sequence_unchanged':True,
                        'selected_ids_scores_order_query_headers_unchanged':True,
                        'original_context_utf8_bytes':len(row['context'].encode()),'layout_context_utf8_bytes':len(layout_context.encode())})
    if seen_cells != set(by_cell):
        raise ValueError('layout cells differ from original retrieval cells')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as handle:
        for row in new_contexts:
            handle.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')
    result = {'schema_version':'layout_contexts_v0.13','rendered_at_utc':datetime.now(timezone.utc).isoformat(),
              'script_sha256':sha(Path(__file__).read_bytes()),'protocol_path':str(protocol_path),
              'protocol_sha256':sha(protocol_path.read_bytes()),'input_hashes':{key:r['sha256'] for key,r in inputs.items()},
              'external_contexts_path':str(output),'external_contexts_sha256':sha(output.read_bytes()),
              'context_count':len(new_contexts),'unique_selected_chunk_count':len(restored_by_id),
              'unique_source_page_count':len(page_cache),'all_invariants_pass':True,
              'native_token_counts_verified':False,'native_execution_eligible':False,
              'next_required_gate':'Recount every complete native prompt; any input over3500 tokens blocks whole proposed layout batch without truncation.',
              'scientific_claim':'Context rendering only; no predictions or performance claim.',
              'records':records,'span_receipts':sorted(span_receipts,key=lambda r:r['chunk_id'])}
    metadata_output.parent.mkdir(parents=True,exist_ok=True)
    metadata_output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol',type=Path,default=DEFAULT_PROTOCOL)
    parser.add_argument('--contexts',type=Path,default=DEFAULT_CONTEXTS)
    parser.add_argument('--metadata',type=Path,default=DEFAULT_METADATA)
    args=parser.parse_args()
    result=run(args.protocol,args.contexts,args.metadata)
    print(json.dumps({key:result[key] for key in ('context_count','unique_selected_chunk_count','unique_source_page_count','all_invariants_pass','external_contexts_sha256')}))


if __name__=='__main__':
    main()

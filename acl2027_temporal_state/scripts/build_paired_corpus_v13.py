#!/usr/bin/env python3
"""Build a page-bounded development corpus from four already captured PDFs.

Source text, OCR and transcriptions stay outside the repository. This builder
does not fetch sources or create/repair transcriptions. Its manifest contains
locators, hashes and counts, never full third-party passages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE = Path('/workspace/scratch/bdef663e3dfc/tmp/access_pilot_v12')
DEFAULT_OUTPUT = Path('/workspace/scratch/bdef663e3dfc/tmp/paired_reader_v13/corpus')
SOURCES = (
    ('plug_2019_10k', 'plug_power_2018_restatement', 1, 142, '340fa876da758ee17acf58d1a75f93bcdea1307f4b5a07c6daeb6ec88923cd47'),
    ('plug_2020_10k', 'plug_power_2018_restatement', 2, 347, '9d70c8184bbb6d4b66b7a2b2b989e44c2c105686e41b04f11d4bd8f9506b2ef6'),
    ('opera_pdf_v1', 'opera_1109_4897', 1, 24, 'c548507189d5d8e880c48cd57471a88857f9a8ec2961cd4cae79ca12ee59bec0'),
    ('opera_pdf_v4', 'opera_1109_4897', 2, 37, '69a5560d00e22b617838908ff85084a69a37baab69d22d8b11733d9c2ea24137'),
)
MAX_WORDS = 160
OVERLAP_WORDS = 30


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return digest(path.read_bytes())


def tool_receipt(path: str) -> dict:
    result = subprocess.run([path, '--version' if 'tesseract' in path else '-v'], capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError(f'tool version failed: {path}')
    return {'path': path, 'executable_sha256': file_sha(Path(path)),
            'version': (result.stdout + result.stderr).decode('utf-8', 'replace').splitlines()[0]}


def split_pages(layout: str, expected_count: int) -> list[str]:
    """Use actual PDF form-feed boundaries, preserving even empty pages."""
    pages = layout.split('\f')
    if pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != expected_count:
        raise ValueError(f'page count mismatch: expected {expected_count}, got {len(pages)}')
    return pages


def page_chunks(text: str, max_words: int = MAX_WORDS, overlap: int = OVERLAP_WORDS):
    if not 0 <= overlap < max_words:
        raise ValueError('overlap must be smaller than a positive chunk size')
    words = text.split()
    start = 0
    while start < len(words):
        stop = min(start + max_words, len(words))
        yield start, stop, ' '.join(words[start:stop])
        if stop == len(words):
            break
        start = stop - overlap


def table_supplement(native: str, table: dict) -> str:
    """Insert separately tagged full-table text after its native caption."""
    if not table.get('whole_table') or not table.get('visual_review_performed'):
        raise ValueError('unreviewed or partial table transcription')
    lines = native.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if table['insert_after_caption_containing'] in line]
    if len(matches) != 1:
        raise ValueError('table caption must match exactly one native line')
    marker = '[BEGIN MODEL-ASSISTED VISUAL WHOLE-TABLE TRANSCRIPTION; NOT NATIVE PDF TEXT]\n'
    supplement = '\n' + marker + table['text'] + '\n[END VISUAL TABLE TRANSCRIPTION]\n'
    lines.insert(matches[0] + 1, supplement)
    return ''.join(lines)


def load_transcriptions(path: Path, output: Path) -> tuple[dict, dict]:
    payload = json.loads(path.read_text())
    if payload['source_pdf_sha256'] != SOURCES[2][-1]:
        raise ValueError('transcription source mismatch')
    tables = payload['tables']
    if {(t['document_id'], t['pdf_page']) for t in tables} != {('opera_pdf_v1', 15), ('opera_pdf_v1', 20)} or len(tables) != 2:
        raise ValueError('the two known image-only tables must both be provided')
    lookup = {}
    for table in tables:
        if digest(table['text'].encode()) != table['text_sha256']:
            raise ValueError('transcription text hash mismatch')
        for kind in ('render', 'machine_ocr'):
            asset = (output / table[f'{kind}_path']).resolve()
            if not asset.is_relative_to(output.resolve()) or file_sha(asset) != table[f'{kind}_sha256']:
                raise ValueError(f'{kind} provenance mismatch')
        lookup[(table['document_id'], table['pdf_page'])] = table
    return payload, lookup


def build(capture_root: Path, output: Path, manifest_path: Path, transcript_path: Path) -> dict:
    output = output.resolve()
    if output.is_relative_to(ROOT.parent):
        raise ValueError('full third-party corpus must stay outside the repository')
    if (output / 'chunks.jsonl').exists() or manifest_path.exists():
        raise FileExistsError('refusing to overwrite corpus or manifest; use a new recorded version')
    transcript_payload, transcripts = load_transcriptions(transcript_path, output)
    output.mkdir(parents=True, exist_ok=True)
    tools = {name: tool_receipt('/usr/bin/' + name) for name in ('pdftotext', 'pdftoppm', 'tesseract')}
    documents, chunks = [], []
    for doc_id, history_id, version_order, expected_pages, expected_sha in SOURCES:
        source = capture_root / doc_id / 'response.bin'
        if file_sha(source) != expected_sha:
            raise ValueError(f'source hash mismatch: {doc_id}')
        docdir = output / doc_id
        docdir.mkdir(exist_ok=False)
        layout_file = docdir / 'native_layout.txt'
        subprocess.run(['/usr/bin/pdftotext', '-layout', '-enc', 'UTF-8', str(source), str(layout_file)], check=True, timeout=60)
        native_pages = split_pages(layout_file.read_text(), expected_pages)
        page_records = []
        for number, native in enumerate(native_pages, 1):
            table = transcripts.get((doc_id, number))
            page_text = table_supplement(native, table) if table else native
            provenance = ['native_pdftotext_layout'] + (['tagged_model_assisted_whole_table_transcription'] if table else [])
            page_file = docdir / f'p{number:03}.txt'
            page_file.write_text(page_text)
            current_chunks = []
            for index, (start, stop, text) in enumerate(page_chunks(page_text), 1):
                item = {'chunk_id': f'{doc_id}:p{number:03}:c{index:02}', 'history_id': history_id,
                        'document_id': doc_id, 'version_order': version_order, 'pdf_page': number,
                        'chunk_index': index, 'word_start_0based': start, 'word_stop_exclusive': stop,
                        'word_count': stop-start, 'text_sha256': digest(text.encode()), 'provenance': provenance,
                        'text': text}
                chunks.append(item)
                current_chunks.append({k:v for k,v in item.items() if k not in ('text', 'history_id', 'document_id', 'version_order', 'pdf_page', 'provenance')})
            page_records.append({'pdf_page': number, 'native_text_sha256': digest(native.encode()),
                                 'corpus_page_sha256': file_sha(page_file), 'native_word_count': len(native.split()),
                                 'corpus_word_count': len(page_text.split()), 'provenance': provenance,
                                 'chunks': current_chunks})
        documents.append({'document_id': doc_id, 'history_id': history_id, 'version_order': version_order,
                          'source_pdf_sha256': expected_sha, 'pdf_page_count': expected_pages,
                          'native_layout_sha256': file_sha(layout_file), 'pages': page_records})
    chunks_file = output / 'chunks.jsonl'
    with chunks_file.open('x') as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + '\n')
    tables_public = [{k:v for k,v in t.items() if k not in ('text', 'insert_after_caption_containing')} for t in transcript_payload['tables']]
    manifest = {'schema_version': 'paired_corpus_v0.13', 'script_sha256': file_sha(Path(__file__)),
                'source_receipt_sha256': file_sha(ROOT / 'results/access_pilot_v12.json'),
                'external_corpus_root': str(output), 'chunks_file': 'chunks.jsonl', 'chunks_sha256': file_sha(chunks_file),
                'chunking': {'max_whitespace_words': MAX_WORDS, 'overlap_whitespace_words': OVERLAP_WORDS, 'cross_page': False},
                'scope': 'complete PDF native-text access with two explicitly tagged image-table supplements; not faithful OCR of all figures or equations',
                'native_math_limitations': ['Formula layout and superscripts may flatten.', 'Other raster figure contents are not guaranteed searchable.'],
                'table_transcription': {'external_file': str(transcript_path), 'file_sha256': file_sha(transcript_path),
                                        'tables': tables_public, 'limitations': transcript_payload['limitations'],
                                        'render_command': transcript_payload['render_command'], 'ocr_command': transcript_payload['ocr_command']},
                'tools': tools, 'source_pdf_count': len(documents), 'history_count': 2,
                'pdf_page_count': sum(d['pdf_page_count'] for d in documents), 'chunk_count': len(chunks),
                'documents': documents}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-root', type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/paired_reader_v13/corpus_manifest.json')
    parser.add_argument('--transcriptions', type=Path)
    args = parser.parse_args()
    manifest = build(args.capture_root, args.output, args.manifest, args.transcriptions or args.output / 'table_transcriptions.json')
    print(json.dumps({k:manifest[k] for k in ('source_pdf_count','history_count','pdf_page_count','chunk_count','chunks_sha256')}))


if __name__ == '__main__':
    main()

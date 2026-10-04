#!/usr/bin/env python3
"""Render exact source fragments for inspection, never a reconstructed value table."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from temporal_state import typed_reader_v15_1 as byte_reader

SCHEMA = 'bounded_source_block_render_v16'
XH = '{http://www.w3.org/1999/xhtml}'
ALLOWED = {XH + s for s in ('table', 'tr', 'td', 'th', 'p', 'div', 'span',
                           'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article')}
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_FRAGMENT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 100_000_000
LIMITATIONS = [
    'inspection_fragment_not_full_browser_rendering',
    'original_source_siblings_omitted_and_layout_may_change',
    'source_css_support_is_pymupdf_story_subset_not_browser_cascade',
    'external_stylesheets_images_fonts_not_loaded_archive_is_none',
    'built_in_font_substitution_may_change_glyphs_and_layout',
    'no_certification_of_visibility_entity_period_unit_or_financial_scope',
    'no_typed_numeric_values_added_or_source_labels_invented',
    'matching_output_text_does_not_prove_original_rendering_fidelity',
]


class RenderError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with Path(path).open('rb') as stream:
        h = hashlib.sha256()
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def encode(record):
    return (json.dumps(record, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def write_new(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)


def bound_anchor(source, node, *, opening=False):
    stop = node.opening_stop if opening else node.stop
    return {'byte_start': node.start, 'byte_stop': stop,
            'span_sha256': sha(source[node.start:stop])}


def qname(source, node):
    match = re.match(rb'<([^\s/>]+)', source[node.start:node.opening_stop])
    if match is None:
        raise RenderError('invalid_opening_tag')
    return match.group(1)


def prepare_blocks(source, selection):
    """Check all selections before any output. Return exact source-derived HTML."""
    if not isinstance(source, bytes) or len(source) > MAX_SOURCE_BYTES:
        raise RenderError('source_size_or_type_outside_profile')
    if (sha(source) != selection.get('source_sha256')
            or len(source) != selection.get('source_bytes')):
        raise RenderError('source_identity_mismatch')
    blocks = selection.get('blocks')
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 16:
        raise RenderError('block_count_outside_profile')
    nodes = byte_reader._parse(source)
    indexed = {n.path: n for n in nodes}
    heads = [n for n in nodes if n.tag == XH + 'head']
    head_styles = [n for n in nodes if n.tag == XH + 'style'
                   and any(p in heads for p in n.ancestors())]
    source_css = b''.join(source[n.start:n.stop] for n in head_styles)
    out_of_head_styles = sum(n.tag == XH + 'style' and n not in head_styles for n in nodes)
    external_css = sum(n.tag == XH + 'link' and
                       'stylesheet' in n.attrs.get('rel', '').lower().split() for n in nodes)
    results, used_ids = [], set()
    for block in blocks:
        block_id = block.get('block_id')
        if not isinstance(block_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', block_id):
            raise RenderError('unsafe_block_id')
        if block_id in used_ids:
            raise RenderError('duplicate_block_id')
        used_ids.add(block_id)
        node = indexed.get(block.get('dom_path'))
        if node is None or node.tag not in ALLOWED:
            raise RenderError('block_locator_or_element_outside_profile')
        if block.get('anchor') != bound_anchor(source, node):
            raise RenderError('block_span_hash_or_boundaries_mismatch')
        fragment = source[node.start:node.stop]
        ancestors = list(reversed(list(node.ancestors())))
        if not ancestors or ancestors[0].tag != XH + 'html':
            raise RenderError('xhtml_document_root_required')
        if not any(n.tag == XH + 'body' for n in ancestors):
            raise RenderError('block_outside_body')
        if any(n.tag.startswith(XH) and b':' in qname(source, n)
               for n in [*ancestors, *list(node.walk()), *head_styles]):
            raise RenderError('prefixed_xhtml_unsupported_by_html_renderer')
        # Use exact source opening tags; no siblings or invented source labels.
        # Generated closing tags solely balance the retained ancestor wrappers.
        first = ancestors[0]
        parts = [source[first.start:first.opening_stop], b'<head>', source_css, b'</head>']
        parts.extend(source[n.start:n.opening_stop] for n in ancestors[1:])
        parts.append(fragment)
        parts.extend(b'</' + qname(source, n) + b'>' for n in reversed(ancestors))
        assembled = b''.join(parts)
        if len(assembled) > MAX_FRAGMENT_BYTES:
            raise RenderError('fragment_size_outside_profile')
        # Strict UTF-8 decoding; the frozen parser also enforces ASCII declarations.
        assembled.decode('utf-8')
        results.append({'block_id': block_id, 'dom_path': node.path,
                        'anchor': bound_anchor(source, node), 'fragment': fragment,
                        'html': assembled,
                        'ancestor_openings': [dict(dom_path=n.path,
                            **bound_anchor(source, n, opening=True)) for n in ancestors],
                        'head_stylesheets': [dict(dom_path=n.path, **bound_anchor(source, n))
                                             for n in head_styles],
                        'out_of_head_style_blocks_not_added': out_of_head_styles,
                        'external_stylesheet_links_not_loaded': external_css,
                        'fragment_image_nodes': sum(n.tag == XH + 'img' for n in node.walk()),
                        'fragment_script_nodes_not_executed': sum(n.tag == XH + 'script' for n in node.walk())})
    return results


def _render_worker(html_path, pdf_path, header_identity, max_pages):
    """Separate bounded-lifetime process. No Archive, networking or source lookup."""
    import fitz
    html = Path(html_path).read_text(encoding='utf-8')
    story = fitz.Story(html=html, user_css=None, em=12, archive=None)
    buffer = io.BytesIO()
    writer = fitz.DocumentWriter(buffer)
    media = fitz.paper_rect('a4-l')
    where = fitz.Rect(28, 76, media.width - 28, media.height - 28)
    more, pages, placed = True, 0, []
    try:
        while more and pages < max_pages:
            device = writer.begin_page(media)
            more, filled = story.place(where)
            story.draw(device)
            writer.end_page()
            pages += 1
            placed.append({'page': pages, 'filled_rectangle': list(filled),
                           'more_content_after_page': bool(more)})
    finally:
        writer.close()
    doc = fitz.open(stream=buffer.getvalue(), filetype='pdf')
    texts, outside_words = [], 0
    for index, page in enumerate(doc):
        # Headers are added independently of source CSS and clearly designated.
        page.insert_text((28, 24), 'MACHINE-GENERATED SOURCE INSPECTION HEADER', fontsize=8)
        page.insert_text((28, 37), header_identity + f' | page {index + 1}', fontsize=8)
        page.insert_text((28, 50), 'Fragment view; browser layout, fonts, external resources and scope are not verified.', fontsize=8)
        page.draw_line((28, 60), (media.width - 28, 60), color=(0.65, 0.65, 0.65), width=0.5)
        texts.append(page.get_text())
        for word in page.get_text('words'):
            if word[0] < 0 or word[1] < 0 or word[2] > media.width or word[3] > media.height:
                outside_words += 1
    # O_EXCL reserves the only final PDF path; save() is never used on a prior file.
    write_new(pdf_path, doc.tobytes(garbage=3, deflate=True))
    write_new(Path(pdf_path).with_suffix('.txt'), '\n\f\n'.join(texts).encode())
    doc.close()
    return {'status': 'page_bound_reached' if more else 'rendered_inspection_view',
            'complete_story_pagination': not bool(more), 'pages': pages,
            'placement': placed, 'extracted_words_outside_media': outside_words,
            'visual_review_required': True, 'pymupdf_version': fitz.__version__}


def render_selection(source_path, selection, external_output, *, max_pages=8,
                     timeout_seconds=120, png=False):
    if type(max_pages) is not int or not 1 <= max_pages <= 12:
        raise RenderError('max_pages_outside_profile')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 180:
        raise RenderError('timeout_outside_profile')
    output = Path(external_output).resolve()
    git_root = ROOT.parent.resolve()
    if output == git_root or git_root in output.parents:
        raise RenderError('source_bearing_output_inside_git_forbidden')
    source = Path(source_path).read_bytes()
    prepared = prepare_blocks(source, selection)
    output.mkdir(parents=True, exist_ok=False)
    record = {'schema_version': SCHEMA, 'started_at_utc': utc(),
              'source_sha256': sha(source), 'source_bytes': len(source),
              'selection_sha256': sha(encode(selection)),
              'archive': None, 'resource_fetch_layer': None,
              'limitations': LIMITATIONS, 'blocks': [], 'max_pages_per_block': max_pages,
              'max_output_bytes': MAX_OUTPUT_BYTES, 'worker_timeout_seconds': timeout_seconds}
    write_new(output / 'start.json', encode(record))
    for item in prepared:
        block_id = item['block_id']
        html_path = output / (block_id + '.html')
        pdf_path = output / (block_id + '.pdf')
        write_new(html_path, item['html'])
        write_new(output / (block_id + '.source-fragment.xml'), item['fragment'])
        entry = {k: v for k, v in item.items() if k not in ('html', 'fragment')}
        before = time.monotonic()
        command = [sys.executable, str(Path(__file__).resolve()), '--worker',
                   str(html_path), str(pdf_path),
                   sha(source)[:20] + ' | block ' + block_id, str(max_pages)]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=timeout_seconds,
                                       check=False)
            # Renderer diagnostics may contain source-bearing resource names; external only.
            write_new(output / (block_id + '.worker-stdout.txt'), completed.stdout)
            write_new(output / (block_id + '.worker-stderr.txt'), completed.stderr)
            entry['worker_exit_code'] = completed.returncode
            worker_receipt = output / (block_id + '.worker.json')
            if completed.returncode or not worker_receipt.exists():
                entry['status'] = 'worker_failed'
            else:
                entry.update(json.loads(worker_receipt.read_text()))
            if png and pdf_path.exists():
                raster = subprocess.run(['pdftoppm', '-r', '110', '-png', str(pdf_path),
                                         str(output / block_id)], capture_output=True,
                                        timeout=timeout_seconds, check=False)
                write_new(output / (block_id + '.raster-stdout.txt'), raster.stdout)
                write_new(output / (block_id + '.raster-stderr.txt'), raster.stderr)
                entry['raster_exit_code'] = raster.returncode
                if raster.returncode:
                    entry['status'] = 'raster_failed'
        except subprocess.TimeoutExpired:
            entry['status'] = 'worker_or_raster_timeout'
        except OSError as exc:
            entry['status'] = 'process_launch_error'
            entry['error_type'] = type(exc).__name__
        entry['elapsed_seconds'] = time.monotonic() - before
        entry['artifacts'] = [dict(path=p.name, bytes=p.stat().st_size, sha256=digest(p))
                              for p in sorted(output.glob(block_id + '.*')) if p.is_file()]
        entry['artifacts'].extend(dict(path=p.name, bytes=p.stat().st_size, sha256=digest(p))
                                  for p in sorted(output.glob(block_id + '-*.png')))
        record['blocks'].append(entry)
        if sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > MAX_OUTPUT_BYTES:
            entry['status'] = 'output_byte_bound_exceeded_partial_files_retained'
            break
    record['finished_at_utc'] = utc()
    record['requested_blocks'] = len(prepared)
    record['processed_blocks'] = len(record['blocks'])
    record['status'] = ('rendered_inspection_views_pending_visual_review'
                        if len(record['blocks']) == len(prepared)
                        and all(b['status'] == 'rendered_inspection_view' for b in record['blocks'])
                        else 'incomplete_or_failed')
    write_new(output / 'receipt.json', encode(record))
    return record


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        _, _, html, pdf, identity, pages = sys.argv
        result = _render_worker(html, pdf, identity, int(pages))
        write_new(Path(pdf).with_suffix('.worker.json'), encode(result))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--blocks-sha256', required=True)
    parser.add_argument('--external-output', type=Path, required=True)
    parser.add_argument('--max-pages', type=int, default=8)
    parser.add_argument('--timeout-seconds', type=int, default=120)
    parser.add_argument('--png', action='store_true')
    args = parser.parse_args()
    if digest(args.source) != args.source_sha256 or digest(args.blocks) != args.blocks_sha256:
        parser.error('source or block-selection file hash mismatch')
    selection = json.loads(args.blocks.read_text())
    if selection.get('source_sha256') != args.source_sha256:
        parser.error('selection/source hash mismatch')
    record = render_selection(args.source, selection, args.external_output,
                              max_pages=args.max_pages, timeout_seconds=args.timeout_seconds,
                              png=args.png)
    print(json.dumps({'status': record['status'], 'blocks': len(record['blocks']),
                      'receipt': str(args.external_output / 'receipt.json')}))
    return 0 if record['status'] == 'rendered_inspection_views_pending_visual_review' else 1


if __name__ == '__main__':
    raise SystemExit(main())

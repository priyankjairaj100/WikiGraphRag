#!/usr/bin/env python3
"""Read-only audit of frozen compatibility artifacts; source worksheets stay external.

This checker never invokes the compatibility transform or any renderer. It replays
the saved patch ledger, compares exact input bytes, and reads existing PDF text.
Mechanical checks never admit a view or validate financial/identity semantics.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gc
import hashlib
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
GIT_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / 'src'))
from temporal_state import typed_reader_v15_1 as byte_reader
from lxml import etree

XH = '{http://www.w3.org/1999/xhtml}'
IX = '{http://www.xbrl.org/2013/inlineXBRL}'
MAX_SOURCE_BYTES = 25 * 1024 ** 2
MAX_ARTIFACT_BYTES = 25 * 1024 ** 2
CSS_WS = ' \t\r\n\f'
ATTR = re.compile(rb'([^\s=<>/]+)\s*=\s*([\'"])(.*?)\2', re.S)
RGBA = re.compile(r'rgba\([ \t\r\n\f]*([0-9]{1,3})[ \t\r\n\f]*,[ \t\r\n\f]*([0-9]{1,3})[ \t\r\n\f]*,[ \t\r\n\f]*([0-9]{1,3})[ \t\r\n\f]*,[ \t\r\n\f]*(1|100%|0\.01|0)[ \t\r\n\f]*\)', re.I)
RISKS = {'display', 'visibility', 'position', 'z-index', 'opacity', 'overflow',
         'overflow-x', 'overflow-y', 'clip', 'clip-path', 'transform', 'direction',
         'writing-mode', 'order', 'flex', 'grid', 'content', 'top', 'bottom',
         'left', 'right', '-sec-ix-hidden'}
META = b'<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'


class ReviewError(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise ReviewError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def no_symlink(path):
    path = Path(path).absolute()
    need(not any(p.is_symlink() for p in [path, *path.parents]), 'symlink_path_refused')
    return path.resolve()


def record(path):
    path = Path(path)
    raw = path.read_bytes()
    return {'path': str(path), 'filename': path.name, 'bytes': len(raw), 'sha256': sha(raw)}


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')


def load_bound(path, expected):
    path = no_symlink(path)
    raw = path.read_bytes()
    need(sha(raw) == expected, 'declared_input_hash_mismatch')
    return json.loads(raw), record(path)


def anchor(source, node):
    return {'byte_start': node.start, 'byte_stop': node.stop,
            'span_sha256': sha(source[node.start:node.stop])}


def opening(source, node):
    return {'dom_path': node.path, 'byte_start': node.start,
            'byte_stop': node.opening_stop,
            'span_sha256': sha(source[node.start:node.opening_stop])}


def qname(source, node):
    return re.match(rb'<([^\s/>]+)', source[node.start:node.opening_stop]).group(1)


def independent_paths(root):
    paths = {}
    def visit(node, path):
        paths[path] = node
        counts = Counter()
        for child in node:
            if not isinstance(child.tag, str):
                continue
            counts[child.tag] += 1
            visit(child, path + '/' + child.tag + '[' + str(counts[child.tag]) + ']')
    visit(root, '/' + root.tag + '[1]')
    return paths


def source_text(node):
    def visit(current):
        parts = []
        for child in current.parts:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag == XH + 'br':
                parts.append('\n')
            elif child.tag not in {XH + 'script', XH + 'style'}:
                block = child.tag in {XH + x for x in ['p', 'div', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']}
                if block:
                    parts.append('\n')
                parts.extend(visit(child))
                if block:
                    parts.append('\n')
        return parts
    return ''.join(visit(node))


def without_whitespace(text):
    # Exact remaining code points: no case-fold, Unicode normalization, number repair,
    # punctuation stripping, sign substitution, or inferred reading order.
    return ''.join(char for char in text if not char.isspace())


def css_declarations(css):
    """Collect inline declarations while preserving all original text externally."""
    result, start, i, depth, quote = [], 0, 0, 0, None
    while i < len(css):
        if quote:
            if css[i] == quote:
                quote = None
            i += 1
            continue
        if css.startswith('/*', i):
            stop = css.find('*/', i + 2)
            need(stop >= 0, 'unterminated_css_comment')
            i = stop + 2
            continue
        ch = css[i]
        if ch in '\'"':
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif ch == ';' and depth == 0:
            result.append((start, css[start:i]))
            start = i + 1
        i += 1
    if start < len(css):
        result.append((start, css[start:]))
    return result


def decode_xml_attribute(raw):
    text = raw.decode('utf-8')
    chars, spans, pos, offset = [], [], 0, 0
    while pos < len(text):
        begin = offset
        if text[pos] == '&':
            stop = text.find(';', pos)
            need(stop >= 0, 'attribute_entity_unterminated')
            token = text[pos:stop + 1]
            char = html.unescape(token)
            need(len(char) == 1, 'attribute_entity_outside_xml_profile')
            offset += len(token.encode('utf-8'))
            pos = stop + 1
        elif text[pos:pos + 2] == '\r\n':
            char, pos, offset = ' ', pos + 2, offset + 2
        else:
            char = ' ' if text[pos] in '\r\n\t' else text[pos]
            offset += len(text[pos].encode('utf-8'))
            pos += 1
        chars.append(char)
        spans.append((begin, offset))
    return ''.join(chars), spans


def eligible_rgba_positions(assembly):
    """Inspect saved source tokens, without invoking or writing a new transform."""
    tokens, eligible = [], {}
    for node in byte_reader._parse(assembly):
        raw_opening = assembly[node.start:node.opening_stop]
        for attribute in ATTR.finditer(raw_opening):
            name = attribute.group(1).decode('utf-8')
            if name.lower() != 'style':
                continue
            raw = attribute.group(3)
            css, mapping = decode_xml_attribute(raw)
            need(css == node.attrs[name], 'style_attribute_decoding_mismatch')
            base = node.start + attribute.start(3)
            for start, declaration in css_declarations(css):
                colon = declaration.find(':')
                if colon < 0:
                    continue
                prop = declaration[:colon].strip(CSS_WS).lower()
                value = declaration[colon + 1:]
                # Mask quoted/commented spans before finding literal function tokens.
                mask = list(value)
                i, quote = 0, None
                while i < len(value):
                    if quote:
                        char = value[i]
                        mask[i] = ' '
                        if char == quote:
                            quote = None
                        i += 1
                        continue
                    if value.startswith('/*', i):
                        end = value.find('*/', i + 2)
                        need(end >= 0, 'unterminated_css_comment')
                        mask[i:end + 2] = ' ' * (end + 2 - i)
                        i = end + 2
                        continue
                    if value[i] in '\'"':
                        quote = value[i]
                        mask[i] = ' '
                    i += 1
                masked = ''.join(mask)
                for match in RGBA.finditer(masked):
                    prev = masked[match.start() - 1] if match.start() else ''
                    if prev and (prev.isalnum() or ord(prev) >= 128 or prev in '-_#'):
                        continue
                    need(all(int(match.group(k)) <= 255 for k in [1, 2, 3]), 'rgba_channel_range_mismatch')
                    absolute = start + colon + 1
                    left, right = absolute + match.start(), absolute + match.end()
                    raw_token = raw[mapping[left][0]:mapping[right - 1][1]]
                    need(raw_token.decode('utf-8') == value[match.start():match.end()], 'encoded_rgba_token_in_saved_ledger_input')
                    token = {'property': prop, 'alpha': match.group(4), 'assembly_dom_path': node.path,
                             'original_token': raw_token.decode('utf-8')}
                    tokens.append(token)
                    if match.group(4) == '1':
                        alpha_pos = absolute + match.start(4)
                        pos = base + mapping[alpha_pos][0]
                        stop = base + mapping[alpha_pos][1]
                        need(pos not in eligible, 'duplicate_expected_patch_position')
                        eligible[pos] = {'byte_stop': stop, 'property': prop, 'assembly_dom_path': node.path}
    return tokens, eligible


def audit_ledger(assembly, derived, ledger):
    need(ledger['schema_version'] == 'inline_rgba_alpha_compatibility_v17_2', 'ledger_schema_mismatch')
    need(ledger['original_assembly_bytes'] == len(assembly) and ledger['original_assembly_sha256'] == sha(assembly), 'ledger_original_identity_mismatch')
    need(ledger['derived_assembly_bytes'] == len(derived) and ledger['derived_assembly_sha256'] == sha(derived), 'ledger_derived_identity_mismatch')
    patches = ledger['patches']
    need(len(patches) == ledger['patch_count'] and len(patches) <= 512, 'ledger_patch_count_mismatch')
    tokens, eligible = eligible_rgba_positions(assembly)
    need(len(tokens) == ledger['checked_rgba_tokens'] <= 2048, 'ledger_checked_token_count_mismatch')
    need(len(eligible) == len(patches), 'ledger_alpha_one_population_mismatch')
    chunks, cursor = [], 0
    for patch in patches:
        start, stop = patch['byte_start'], patch['byte_stop']
        need(type(start) is int and type(stop) is int and cursor <= start < stop <= len(assembly), 'ledger_interval_order_or_bound_mismatch')
        need(stop - start == 1 and assembly[start:stop] == b'1', 'ledger_old_byte_mismatch')
        need(patch['old_literal'] == '1' and patch['new_literal'] == '100%', 'ledger_literals_mismatch')
        need(eligible.get(start) == {key: patch[key] for key in ['byte_stop', 'property', 'assembly_dom_path']}, 'ledger_patch_not_exact_inline_alpha_token')
        chunks += [assembly[cursor:start], b'100%']
        cursor = stop
    chunks.append(assembly[cursor:])
    need(b''.join(chunks) == derived, 'ledger_nonpatch_byte_preservation_mismatch')
    need(len(derived) == len(assembly) + 3 * len(patches), 'ledger_length_change_mismatch')
    need(ledger['all_nonpatch_bytes_preserved'] is True and ledger['original_source_fragment_modified'] is False, 'ledger_preservation_declaration_mismatch')
    return {'patch_count': len(patches), 'checked_rgba_tokens': len(tokens),
            'rgba_tokens': tokens, 'all_nonpatch_bytes_verified': True}


def artifact(path_record, attempt_root):
    path = no_symlink(path_record['path'])
    need(attempt_root in path.parents, 'artifact_outside_bound_attempt')
    need(path.is_file() and path.stat().st_size <= MAX_ARTIFACT_BYTES, 'artifact_missing_or_oversize')
    raw = path.read_bytes()
    need(len(raw) == path_record['bytes'] and sha(raw) == path_record['sha256'], 'artifact_hash_mismatch')
    return raw


def describe_css(source, node, ancestors):
    styles, risks, hidden, properties = [], [], [], defaultdict(set)
    for current in [*ancestors, *node.walk()]:
        css = current.attrs.get('style', '')
        declarations = []
        for _, declaration in css_declarations(css):
            if ':' in declaration:
                key, value = declaration.split(':', 1)
                key, value = key.strip(CSS_WS).lower(), value.strip(CSS_WS)
                declarations.append((key, value))
                properties[key].add(value)
        if not css:
            continue
        item = {'dom_path': current.path, 'location': 'ancestor_wrapper' if current in ancestors else 'block',
                'opening': opening(source, current), 'style': css, 'declarations': declarations}
        styles.append(item)
        risk = [(key, value) for key, value in declarations if key in RISKS]
        if risk:
            risks.append(dict(item, risk_declarations=risk, source_subtree_text=current.text(),
                              source_numeric_nodes=sum(x.tag == IX + 'nonFraction' for x in current.walk())))
        if any((key == 'display' and value.lower() == 'none') or
               (key == 'visibility' and value.lower() in {'hidden', 'collapse'}) for key, value in declarations):
            hidden.append({'dom_path': current.path, 'non_whitespace_text_characters': len(current.text().strip()),
                           'source_numeric_nodes': sum(x.tag == IX + 'nonFraction' for x in current.walk()),
                           'id_or_class_hook_count': sum('id' in x.attrs or 'class' in x.attrs for x in current.walk())})
    low_border = sum(key.startswith('border') and any(m.group(4) == '0.01' for m in RGBA.finditer(value))
                     for item in styles for key, value in item['declarations'])
    return {'inline_styles': styles, 'risk_declarations': risks, 'hiding_nodes': hidden,
            'property_values': {key: sorted(values) for key, values in sorted(properties.items())},
            'unchanged_low_alpha_rgba_border_declarations': low_border,
            'authored_low_alpha_border_fidelity_failed': True,
            'border_semantic_reliance_review_required': low_border > 0,
            'computed_css_or_browser_equivalence_claimed': False}


def nearest(node, tags):
    return next((ancestor for ancestor in node.ancestors() if ancestor.tag in tags), None)


def table_and_occurrences(source, node, all_fact_ordinals):
    rows, locations = [], {}
    if node.tag == XH + 'table':
        owned_rows = [x for x in node.walk() if x.tag == XH + 'tr' and nearest(x, {XH + 'table'}) is node]
        for row_index, row in enumerate(owned_rows):
            cells = []
            for cell in row.walk():
                if cell.tag not in {XH + 'td', XH + 'th'} or nearest(cell, {XH + 'tr'}) is not row or nearest(cell, {XH + 'table'}) is not node:
                    continue
                cell_index = len(cells)
                locations[cell.path] = (row_index, cell_index)
                cells.append({'cell_index': cell_index, 'dom_path': cell.path, 'anchor': anchor(source, cell),
                              'source_text': source_text(cell), 'raw_descendant_text': cell.text(),
                              'rowspan_declared': cell.attrs.get('rowspan'), 'colspan_declared': cell.attrs.get('colspan'),
                              'attributes': cell.attrs})
            rows.append({'row_index': row_index, 'dom_path': row.path, 'anchor': anchor(source, row), 'cells': cells})
    facts = []
    for fact in node.walk():
        if fact.tag != IX + 'nonFraction':
            continue
        cell = nearest(fact, {XH + 'td', XH + 'th'})
        facts.append({'source_occurrence_ordinal': all_fact_ordinals[fact.path], 'dom_path': fact.path,
                      'anchor': anchor(source, fact), 'raw_source_lexical_text': fact.text(),
                      'source_attributes': fact.attrs, 'row_cell_index': locations.get(cell.path) if cell else None,
                      'source_cell_dom_path': cell.path if cell else None, 'numeric_normalization_used': False})
    return rows, facts


def inspect_block(source, lookup, head_styles, dependency_counts, all_facts,
                  protocol_block, result_block, index_block, attempt_root, output):
    block = protocol_block
    public = {'block_id': block['block_id'], 'role': block['role'], 'dom_path': block['dom_path'],
              'anchor': block['anchor'], 'render_status_preserved': result_block.get('status'),
              'checks': {}, 'reason_codes': [], 'visual_pages_inspected': 0,
              'admission_decision_made': False}
    private = dict(public)
    checks = public['checks']
    try:
        for key in ['block_id', 'dom_path', 'anchor']:
            need(block[key] == result_block[key] == index_block[key], 'block_identity_mismatch')
        need(result_block['status'] == index_block['status'], 'render_status_mismatch')
        node = lookup[block['dom_path']]
        need(anchor(source, node) == block['anchor'], 'original_source_anchor_mismatch')
        checks['original_source_anchor'] = True
        raw_artifacts = {key: artifact(value, attempt_root) for key, value in index_block['artifacts'].items()}
        for png in index_block.get('pngs', []):
            artifact(png, attempt_root)
        receipt = no_symlink(index_block['receipt_path'])
        need(attempt_root in receipt.parents and json.loads(receipt.read_text()) == result_block, 'block_receipt_mismatch')
        checks['available_artifact_hashes_and_receipt'] = True
        private['artifacts'] = index_block['artifacts']
        private['rasters'] = index_block.get('pngs', [])
        public['artifact_bindings'] = index_block['artifacts']
        public['raster_bindings'] = index_block.get('pngs', [])
        public['block_receipt'] = record(receipt)
        ancestors = list(reversed(list(node.ancestors())))
        original_fragment = source[node.start:node.stop]
        expected = (source[ancestors[0].start:ancestors[0].opening_stop] + b'<head>'
                    + b''.join(source[n.start:n.stop] for n in head_styles) + b'</head>'
                    + b''.join(source[n.start:n.opening_stop] for n in ancestors[1:])
                    + original_fragment
                    + b''.join(b'</' + qname(source, n) + b'>' for n in reversed(ancestors)))
        need(raw_artifacts.get('source-fragment.xml') == original_fragment, 'saved_fragment_missing_or_not_exact')
        need(raw_artifacts.get('source-assembly.html') == expected, 'saved_assembly_missing_or_not_exact')
        need(result_block['ancestor_openings'] == [opening(source, n) for n in ancestors], 'ancestor_opening_receipt_mismatch')
        need(result_block['head_stylesheets'] == [dict(dom_path=n.path, **anchor(source, n)) for n in head_styles], 'head_style_receipt_mismatch')
        checks['original_fragment_ancestor_head_style_assembly_bytes'] = True
        private['ancestor_wrappers'] = [{'opening': opening(source, n), 'source_opening': source[n.start:n.opening_stop].decode('utf-8')} for n in ancestors]
        private['source_text'] = source_text(node)
        private['raw_source_descendant_text'] = node.text()
        private['source_css'] = describe_css(source, node, ancestors)
        private['source_rows'], private['source_numeric_occurrences'] = table_and_occurrences(source, node, all_facts)
        css = private['source_css']
        public['source_counts'] = {'raw_text_characters': len(node.text()), 'non_whitespace_text_characters': len(node.text().strip()),
                                   'rows': len(private['source_rows']), 'cells': sum(len(row['cells']) for row in private['source_rows']),
                                   'nonfraction_occurrences': len(private['source_numeric_occurrences']),
                                   'inline_style_nodes': len(css['inline_styles']), 'hiding_nodes': len(css['hiding_nodes']),
                                   'nonempty_hiding_nodes': sum(x['non_whitespace_text_characters'] > 0 or x['source_numeric_nodes'] > 0 for x in css['hiding_nodes']),
                                   'low_alpha_rgba_border_declarations': css['unchanged_low_alpha_rgba_border_declarations']}
        public['css_risk_categories'] = sorted({key for item in css['risk_declarations'] for key, _ in item['risk_declarations']})
        public['source_dependency_counts'] = dependency_counts
        public['reason_codes'].extend(['font_identity_and_full_browser_equivalence_unverified', 'mechanical_checks_do_not_admit_view'])
        if css['border_semantic_reliance_review_required']:
            public['reason_codes'].append('unchanged_low_alpha_border_known_authored_fidelity_failure_visual_reliance_review_required')
        if 'compatibility-patches.json' in raw_artifacts:
            ledger = json.loads(raw_artifacts['compatibility-patches.json'])
            derived = raw_artifacts['compatibility-assembly.html']
            audit = audit_ledger(expected, derived, ledger)
            expected_summary = {key: value for key, value in ledger.items() if key != 'patches'}
            expected_summary['external_patch_ledger'] = index_block['artifacts']['compatibility-patches.json']
            need(result_block['compatibility'] == expected_summary == index_block['compatibility'], 'compatibility_receipt_summary_mismatch')
            need(raw_artifacts.get('source-block.html') == derived.replace(b'<head>', b'<head>' + META, 1), 'final_utf8_render_input_mismatch')
            need(result_block['render_html_sha256'] == sha(raw_artifacts['source-block.html']) and result_block['source_assembly_sha256'] == sha(expected), 'final_input_receipt_hash_mismatch')
            private['ledger_audit'] = audit
            public['compatibility_counts'] = {'patches': audit['patch_count'], 'checked_rgba_tokens': audit['checked_rgba_tokens']}
            checks['ledger_literal_style_positions_and_nonpatch_replay'] = True
            checks['final_render_input_only_utf8_metadata_added'] = True
        else:
            public['reason_codes'].append('compatibility_artifacts_unavailable_preserved')
            checks['ledger_literal_style_positions_and_nonpatch_replay'] = None
            checks['final_render_input_only_utf8_metadata_added'] = None
        has_pdf = 'rendered/source-block.pdf' in raw_artifacts
        rendered = result_block['status'] == 'rendered_pending_full_visual_and_css_review'
        public['rendered_successfully'] = rendered
        if has_pdf:
            import fitz
            with fitz.open(stream=raw_artifacts['rendered/source-block.pdf'], filetype='pdf') as document:
                text_pages = [page.get_text() for page in document]
                private['pdf_words_by_page'] = [page.get_text('words') for page in document]
            pdf_text = '\n\f\n'.join(text_pages)
            private['pdf_text_by_page'] = text_pages
            if 'rendered/source-block.txt' in raw_artifacts:
                need(raw_artifacts['rendered/source-block.txt'] == pdf_text.encode('utf-8'), 'saved_pdf_text_extraction_mismatch')
                checks['saved_pdf_text_matches_bound_pdf_extraction'] = True
            left, right = without_whitespace(private['source_text']), without_whitespace(pdf_text)
            matched = left == right
            public['pdf_source_text_check'] = {'method': 'exact_codepoint_sequence_after_removing_unicode_whitespace_only',
                                              'matches': matched, 'source_codepoints': len(left), 'pdf_codepoints': len(right),
                                              'pdf_pages': len(text_pages), 'visual_fidelity_inferred': False}
            if not matched:
                mismatch = next((i for i, (a, b) in enumerate(zip(left, right)) if a != b), min(len(left), len(right)))
                private['pdf_text_difference'] = {'first_difference_index': mismatch,
                                                 'source_near_difference': left[max(0, mismatch - 50):mismatch + 100],
                                                 'pdf_near_difference': right[max(0, mismatch - 50):mismatch + 100]}
                public['reason_codes'].append('pdf_source_codepoint_sequence_mismatch_requires_visual_review')
            if rendered:
                need(result_block['pages'] == len(text_pages) == len(index_block.get('pngs', [])), 'rendered_page_denominator_mismatch')
                checks['rendered_page_denominator'] = True
        else:
            public['pdf_source_text_check'] = {'available': False, 'matches': None, 'visual_fidelity_inferred': False}
            public['reason_codes'].append('pdf_text_unavailable_original_render_status_preserved')
            need(not rendered, 'successful_status_without_pdf')
        if not rendered:
            public['reason_codes'].append('original_render_failure_not_retried_or_relabelled')
        public['mechanical_binding_status'] = 'available_source_and_compatibility_artifacts_verified'
    except Exception as error:
        public['mechanical_binding_status'] = 'mechanical_check_failed_or_artifact_unavailable'
        public['reason_codes'].append(str(error) if isinstance(error, ReviewError) else 'unexpected_checker_exception_' + type(error).__name__)
        private['checker_exception'] = {'type': type(error).__name__, 'message': str(error)}
    private['public_checks'] = public
    write_new(output / 'worksheet.json', private)
    lines = [block['block_id'], block['dom_path'], 'Source/CSS/ledger worksheet only; no visual or semantic admission.', '',
             'SOURCE TEXT:', private.get('source_text', ''), '', 'PDF TEXT:']
    lines.extend(private.get('pdf_text_by_page', []))
    lines += ['', 'SOURCE ROW/CELL ORDER (all source spans retained):']
    for row in private.get('source_rows', []):
        lines.append('ROW ' + str(row['row_index']))
        for cell in row['cells']:
            lines.append('  CELL ' + str(cell['cell_index']) + ' rowspan=' + repr(cell['rowspan_declared'])
                         + ' colspan=' + repr(cell['colspan_declared']) + ' text=' + repr(cell['source_text']))
    lines += ['', 'SOURCE NUMERIC OCCURRENCE TO CELL MAPPINGS:']
    lines.extend(json.dumps(value, ensure_ascii=False) for value in private.get('source_numeric_occurrences', []))
    with (output / 'worksheet.txt').open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(lines) + '\n')
    public['private_worksheet'] = record(output / 'worksheet.json')
    public['private_readable_worksheet'] = record(output / 'worksheet.txt')
    return public


def inspect_source(source, outcome, index_source, source_root, attempt_root, output):
    need(source['source_path'] == outcome['source_path'] == index_source['source_path'], 'source_population_identity_mismatch')
    need(source['source_sha256'] == outcome['source_sha256'] == index_source['source_sha256'], 'source_sha256_population_mismatch')
    need(len(source['blocks']) == len(outcome['blocks']) == len(index_source['blocks']), 'block_population_count_mismatch')
    filename = source['external_filename']
    need(Path(filename).name == filename, 'unsafe_source_filename')
    path = no_symlink(source_root / filename)
    need(path.stat().st_size <= MAX_SOURCE_BYTES, 'source_size_limit')
    raw = path.read_bytes()
    need(len(raw) == source['source_bytes'] and sha(raw) == source['source_sha256'], 'source_file_identity_mismatch')
    nodes = byte_reader._parse(raw)
    lookup = {node.path: node for node in nodes}
    independent = independent_paths(etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)))
    need(set(lookup) == set(independent), 'independent_dom_locator_population_mismatch')
    need(all(node.tag == independent[path].tag and node.attrs == dict(independent[path].attrib) for path, node in lookup.items()), 'independent_dom_attribute_mismatch')
    heads = [node for node in nodes if node.tag == XH + 'head']
    styles = [node for node in nodes if node.tag == XH + 'style']
    head_styles = [node for node in styles if any(parent in heads for parent in node.ancestors())]
    counts = {'source_elements_independently_compared': len(nodes), 'head_style_elements': len(head_styles),
              'out_of_head_style_elements': len(styles) - len(head_styles),
              'stylesheet_links': sum(node.tag == XH + 'link' and 'stylesheet' in node.attrs.get('rel', '').lower().split() for node in nodes),
              'script_elements': sum(node.tag == XH + 'script' for node in nodes),
              'base_elements': sum(node.tag == XH + 'base' for node in nodes)}
    all_facts = {node.path: ordinal for ordinal, node in enumerate(node for node in nodes if node.tag == IX + 'nonFraction')}
    write_new(output / 'source_css_dependencies.json', {'source_identity': record(path), 'counts': counts,
                                                      'styles': [{'dom_path': node.path, 'text': node.text(), 'anchor': anchor(raw, node)} for node in styles],
                                                      'link_or_script_or_base_attributes': [{'dom_path': node.path, 'attributes': node.attrs} for node in nodes if node.tag in {XH + 'link', XH + 'script', XH + 'base'}]})
    blocks = []
    for i, (block, rendered, index_block) in enumerate(zip(source['blocks'], outcome['blocks'], index_source['blocks'])):
        folder = output / ('block_' + format(i, '02d'))
        folder.mkdir()
        result = inspect_block(raw, lookup, head_styles, counts, all_facts, block, rendered, index_block, attempt_root, folder)
        result['block_index'] = i
        blocks.append(result)
    return blocks, counts, record(output / 'source_css_dependencies.json')


def execute(args):
    source_root = no_symlink(args.sources_root)
    output = no_symlink(args.external_output)
    public = no_symlink(args.public_output)
    attempt_root = no_symlink(Path(args.index).parent)
    need(GIT_ROOT != output and GIT_ROOT not in output.parents, 'source_bearing_output_inside_git_refused')
    need(not output.exists() and output.parent.is_dir(), 'external_output_must_be_fresh_with_existing_parent')
    need(public.parent == ROOT / 'results' and not public.exists(), 'public_output_must_be_new_result_file')
    need(attempt_root not in output.parents and output not in attempt_root.parents and source_root not in output.parents, 'output_overlaps_source_or_attempt')
    protocol, protocol_binding = load_bound(args.protocol, args.protocol_sha256)
    result, result_binding = load_bound(args.result, args.result_sha256)
    index, index_binding = load_bound(args.index, args.index_sha256)
    need(protocol['schema_version'] == 'source_block_lo_inspection_v17_2', 'protocol_schema_mismatch')
    need(result['protocol_sha256'] == index['protocol_sha256'] == protocol_binding['sha256'], 'protocol_execution_index_binding_mismatch')
    need(index['result_sha256'] == result_binding['sha256'], 'execution_index_binding_mismatch')
    need(1 <= len(protocol['sources']) <= 12 and len(protocol['sources']) == len(result['sources']) == len(index['sources']), 'source_denominator_mismatch')
    need(all(len(source['blocks']) <= 16 for source in protocol['sources']), 'source_block_cap')
    total = sum(len(source['blocks']) for source in protocol['sources'])
    need(total == protocol['block_denominator'] == result['requested_blocks'], 'requested_block_denominator_mismatch')
    output.mkdir()
    before = record(Path(__file__).resolve())
    receipt = {'schema_version': 'compatibility_source_binding_review_v17_2',
               'started_at_utc': datetime.now(timezone.utc).isoformat(),
               'protocol': protocol_binding, 'execution': result_binding, 'review_index': index_binding,
               'checker': before, 'protocol_mode': protocol.get('mode'), 'sources': [],
               'requested_sources': len(protocol['sources']), 'requested_blocks': total,
               'mechanical_pass_is_view_admission': False, 'visual_pages_inspected': 0,
               'natural_transform_invocations': 0, 'new_render_or_model_calls': 0,
               'semantic_or_reference_labels_authored': 0,
               'known_preserved_authored_failure': 'Unchanged low-alpha border rendered solid black; this checker cannot establish border fidelity or semantic independence.'}
    for i, (source, outcome, index_source) in enumerate(zip(protocol['sources'], result['sources'], index['sources'])):
        folder = output / ('source_' + format(i, '02d'))
        folder.mkdir()
        entry = {'source_index': i, 'original_source_index': source.get('original_source_index', i),
                 'source_path': source['source_path'], 'source_sha256': source['source_sha256'], 'requested_blocks': len(source['blocks'])}
        try:
            entry['blocks'], entry['dependency_counts'], entry['private_source_css_inventory'] = inspect_source(source, outcome, index_source, source_root, attempt_root, folder)
        except Exception as error:
            reason = str(error) if isinstance(error, ReviewError) else 'unexpected_source_checker_exception_' + type(error).__name__
            write_new(folder / 'source_checker_failure.json', {'type': type(error).__name__, 'message': str(error)})
            entry['source_failure'] = reason
            entry['blocks'] = [{'block_id': block['block_id'], 'role': block['role'], 'dom_path': block['dom_path'],
                                'anchor': block['anchor'], 'block_index': j,
                                'render_status_preserved': outcome['blocks'][j]['status'] if j < len(outcome['blocks']) else 'missing_outcome',
                                'mechanical_binding_status': 'source_check_failed_all_requested_blocks_retained',
                                'reason_codes': [reason], 'admission_decision_made': False}
                               for j, block in enumerate(source['blocks'])]
        receipt['sources'].append(entry)
        gc.collect()
    blocks = [block for source in receipt['sources'] for block in source['blocks']]
    need(len(blocks) == total, 'review_denominator_not_complete')
    receipt['counts'] = {'reviewed_or_retained_blocks': len(blocks),
                         'original_render_status_counts': dict(Counter(block['render_status_preserved'] for block in blocks)),
                         'mechanical_binding_status_counts': dict(Counter(block['mechanical_binding_status'] for block in blocks)),
                         'patches': sum(block.get('compatibility_counts', {}).get('patches', 0) for block in blocks),
                         'pdf_source_text_exact_after_whitespace_removal': sum(block.get('pdf_source_text_check', {}).get('matches') is True for block in blocks),
                         'pdf_source_text_mismatches': sum(block.get('pdf_source_text_check', {}).get('matches') is False for block in blocks),
                         'pdf_source_text_unavailable': sum(block.get('pdf_source_text_check', {}).get('matches') is None for block in blocks),
                         'low_alpha_border_flagged_blocks': sum(block.get('source_counts', {}).get('low_alpha_rgba_border_declarations', 0) > 0 for block in blocks)}
    need(record(Path(__file__).resolve()) == before, 'checker_changed_during_review')
    receipt['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    receipt['status'] = 'all_requested_blocks_retained_mechanical_review_only'
    receipt['limitations'] = ['Exact source/ledger/text transport does not establish visible glyphs, geometry, border fidelity, browser equivalence, identity scope or financial truth.',
                              'Expat byte-parser lineage is shared with extraction; complete lxml expanded locator/attribute concordance adds an independent parser comparison.',
                              'PDF text comparison removes Unicode whitespace only; mismatches remain flagged without repair or inferred reading order.',
                              'Original failed render statuses are retained; no automatic retry or substitution is performed.']
    write_new(output / 'review_summary.json', receipt)
    receipt['private_review_summary'] = record(output / 'review_summary.json')
    write_new(public, receipt)
    print(json.dumps({'public_receipt': record(public), 'counts': receipt['counts']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['protocol', 'protocol-sha256', 'result', 'result-sha256', 'index', 'index-sha256',
                 'sources-root', 'external-output', 'public-output']:
        parser.add_argument('--' + name, required=True)
    execute(parser.parse_args())

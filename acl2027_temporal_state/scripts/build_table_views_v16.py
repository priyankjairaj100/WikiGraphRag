#!/usr/bin/env python3
"""Frozen, question-free source table views with bounded external output."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.parsers.expat as expat
import pyexpat
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from temporal_state import typed_reader_v15_1 as byte_reader

XH = '{http://www.w3.org/1999/xhtml}'
IX = '{http://www.xbrl.org/2013/inlineXBRL}'
TABLE, ROW = XH + 'table', XH + 'tr'
CELLS = {XH + 'td', XH + 'th'}
BLOCKS = {XH + x for x in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6')}
SKIP_TEXT = {XH + 'script', XH + 'style'}
MAX_EXTERNAL_BYTES = 100_000_000
PRIVATE_RESERVE = 65_536
SCHEMA = 'source_table_views_v16'

class ViewError(ValueError):
    pass

class OutputLimitExceeded(ViewError):
    pass

def sha(data):
    return hashlib.sha256(data).hexdigest()

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def utc():
    return datetime.now(timezone.utc).isoformat()

def encoded(record):
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')

def xml_ws(text):
    return re.sub(r'[ \t\r\n]+', ' ', text).strip(' \t\r\n')

def expanded_paths(root):
    """Independent lxml traversal; locators use expanded names, not XPath."""
    paths = {}
    def visit(node, path):
        paths[node] = path
        counts = Counter()
        for child in node:
            if not isinstance(child.tag, str):
                continue
            counts[child.tag] += 1
            visit(child, path + '/' + child.tag + '[' + str(counts[child.tag]) + ']')
    visit(root, '/' + root.tag + '[1]')
    return paths

def ancestors_including(node):
    while node is not None:
        yield node
        node = node.getparent()

def nearest(node, tags, *, include_self=False):
    node = node if include_self else node.getparent()
    while node is not None:
        if node.tag in tags:
            return node
        node = node.getparent()
    return None

def own_text(node):
    """Text nodes + br/block separators; skip nested tables/scripts/styles entirely."""
    def visit(current):
        parts = [current.text or '']
        for child in current:
            if isinstance(child.tag, str):
                if child.tag == XH + 'br':
                    parts.append('\n')
                elif child.tag != TABLE and child.tag not in SKIP_TEXT:
                    if child.tag in BLOCKS:
                        parts.append('\n')
                    parts.extend(visit(child))
                    if child.tag in BLOCKS:
                        parts.append('\n')
            parts.append(child.tail or '')
        return parts
    return xml_ws(''.join(visit(node)))

def visibility(node, paths):
    cues = []
    for item in ancestors_including(node):
        kinds = []
        if item.tag == IX + 'hidden':
            kinds.append('ix_hidden_ancestor_or_self')
        if 'hidden' in item.attrib:
            kinds.append('html_hidden_attribute_ancestor_or_self')
        style = item.attrib.get('style', '')
        if re.search(r'(?:^|;)\s*display\s*:\s*none\s*(?:!\s*important\s*)?(?:;|$)', style, re.I):
            kinds.append('inline_display_none_ancestor_or_self')
        if re.search(r'(?:^|;)\s*visibility\s*:\s*(?:hidden|collapse)\s*(?:!\s*important\s*)?(?:;|$)', style, re.I):
            kinds.append('inline_visibility_hidden_or_collapse_ancestor_or_self')
        if kinds:
            cues.append({'dom_path': paths[item], 'kinds': kinds})
    return {'status': 'markup_hiding_cue' if cues else 'no_recognized_hiding_cue',
            'cues': cues, 'rendering_verified': False, 'computed_css_evaluated': False}

def span_attribute(node, name):
    raw = node.attrib.get(name)
    if raw is None:
        return {'declared': None, 'parsed': 1, 'status': 'absent_default_one'}
    clean = raw.strip(' \t\r\n')
    if not re.fullmatch(r'[0-9]+', clean) or len(clean) > 6:
        return {'declared': raw, 'parsed': None, 'status': 'unresolved_lexical_or_profile_bound'}
    value = int(clean)
    if name == 'rowspan' and value == 0:
        return {'declared': raw, 'parsed': 0, 'status': 'declared_remaining_row_group_unexpanded'}
    if value < 1 or value > 65535:
        return {'declared': raw, 'parsed': None, 'status': 'unresolved_range'}
    return {'declared': raw, 'parsed': value, 'status': 'declared_positive_integer'}

def parse_bound(source):
    # Frozen private helper enforces the v15.1 UTF-8/ASCII and entity policy.
    nodes = byte_reader._parse(source)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             recover=False, huge_tree=False, remove_comments=False)
    root = etree.fromstring(source, parser)
    paths = expanded_paths(root)
    spans = {n.path: n for n in nodes}
    if len(paths) != len(spans) or set(paths.values()) != set(spans):
        raise ViewError('independent_dom_locator_mismatch')
    for node, path in paths.items():
        if node.tag != spans[path].tag or dict(node.attrib) != spans[path].attrs:
            raise ViewError('independent_dom_attribute_mismatch')
    return root, paths, spans

def anchor(node, paths, spans, source):
    item = spans[paths[node]]
    return {'byte_start': item.start, 'byte_stop': item.stop,
            'span_sha256': sha(source[item.start:item.stop])}

def extract_views(source, typed_facts, *, source_path, source_sha256):
    if len(source) > 25 * 1024 * 1024:
        raise ViewError('source_byte_profile_bound_exceeded')
    if sha(source) != source_sha256:
        raise ViewError('source_hash_mismatch')
    root, paths, spans = parse_bound(source)
    all_nodes = list(paths)
    facts = [n for n in all_nodes if n.tag == IX + 'nonFraction']
    if len(facts) != len(typed_facts):
        raise ViewError('typed_fact_population_mismatch')
    fact_ordinals = {}
    for ordinal, (node, typed) in enumerate(zip(facts, typed_facts)):
        expected = anchor(node, paths, spans, source)
        bound = typed.get('anchor', {})
        if (typed.get('fact_ordinal') != ordinal or typed.get('source_sha256') != source_sha256
            or bound.get('source_sha256') != source_sha256 or bound.get('dom_path') != paths[node]
            or any(bound.get(k) != v for k, v in expected.items())):
            raise ViewError('typed_fact_anchor_or_ordinal_mismatch')
        fact_ordinals[node] = ordinal
    tables = [n for n in all_nodes if n.tag == TABLE]
    table_ordinals = {node: i for i, node in enumerate(tables)}
    owned_facts = defaultdict(list)
    for fact in facts:
        owned_facts[nearest(fact, {TABLE})].append(fact)
    owned_rows, owned_cells = defaultdict(list), defaultdict(list)
    for node in all_nodes:
        if node.tag == ROW:
            owned_rows[nearest(node, {TABLE})].append(node)
        elif node.tag in CELLS:
            owned_cells[nearest(node, {TABLE})].append(node)
    paragraphs = [node for node in all_nodes if node.tag in BLOCKS
                  and nearest(node, {TABLE}) is None
                  and not any(d.tag in BLOCKS or d.tag == TABLE for d in node.iterdescendants()
                              if isinstance(d.tag, str))]
    para_ordinals = {node: i for i, node in enumerate(paragraphs)}
    records, public = [], []
    records.append({'record_type': 'source', 'schema_version': SCHEMA,
                    'source_path': source_path, 'source_sha256': source_sha256,
                    'source_bytes': len(source), 'nonfraction_count': len(facts),
                    'table_count': len(tables), 'paragraph_count': len(paragraphs),
                    'outside_table_fact_ordinals': [fact_ordinals[n] for n in owned_facts[None]],
                    'table_ownership': 'nearest_ancestor_table',
                    'text_profile': 'syntactic_block_separators_xml_whitespace_collapse_nbsp_retained_nested_tables_excluded',
                    'rendering_verified': False, 'financial_scope_inferred': False})
    for node in paragraphs:
        records.append({'record_type': 'paragraph', 'paragraph_ordinal': para_ordinals[node],
                        'dom_path': paths[node], 'anchor': anchor(node, paths, spans, source),
                        'text': own_text(node), 'visibility': visibility(node, paths),
                        'semantic_attachment_to_tables': 'not_inferred'})
    for table in tables:
        tp, ti = paths[table], table_ordinals[table]
        parent = nearest(table, {TABLE})
        rows, cell_locations = [], {}
        orphan_cells = []
        for ri, row in enumerate(owned_rows[table]):
            rp = paths[row]
            cells = []
            for cell in owned_cells[table]:
                if nearest(cell, {ROW}) is not row:
                    continue
                ci = len(cells)
                cell_locations[cell] = (ri, ci)
                cells.append({'path_from_row': paths[cell][len(rp):],
                              'tag': cell.tag, 'anchor': anchor(cell, paths, spans, source),
                              'text': own_text(cell), 'rowspan': span_attribute(cell, 'rowspan'),
                              'colspan': span_attribute(cell, 'colspan'),
                              'headers_declared': cell.attrib.get('headers'),
                              'scope_declared': cell.attrib.get('scope'),
                              'visibility': visibility(cell, paths),
                              'nested_table_ordinals': [table_ordinals[t] for t in tables
                                  if nearest(t, CELLS) is cell and nearest(t, {TABLE}) is table]})
            rows.append({'path_from_table': rp[len(tp):], 'anchor': anchor(row, paths, spans, source),
                         'cell_order': 'document_order_no_grid_expansion',
                         'text': '\t'.join(c['text'] for c in cells), 'cells': cells})
        for cell in owned_cells[table]:
            if cell not in cell_locations:
                orphan_cells.append({'path_from_table': paths[cell][len(tp):],
                                     'anchor': anchor(cell, paths, spans, source), 'text': own_text(cell),
                                     'rowspan': span_attribute(cell, 'rowspan'),
                                     'colspan': span_attribute(cell, 'colspan'),
                                     'visibility': visibility(cell, paths)})
        links = []
        for fact in owned_facts[table]:
            cell = nearest(fact, CELLS)
            links.append({'fact_ordinal': fact_ordinals[fact],
                          'path_from_table': paths[fact][len(tp):],
                          'anchor': anchor(fact, paths, spans, source),
                          'row_cell_index': list(cell_locations[cell]) if cell in cell_locations else None})
        tb = anchor(table, paths, spans, source)
        before = [p for p in paragraphs if spans[paths[p]].stop <= tb['byte_start']][-2:]
        after = [p for p in paragraphs if spans[paths[p]].start >= tb['byte_stop']][:2]
        item = {'record_type': 'table', 'table_ordinal': ti, 'dom_path': tp, 'anchor': tb,
                'parent_table_ordinal': table_ordinals.get(parent),
                'child_table_ordinals': [table_ordinals[t] for t in tables if nearest(t, {TABLE}) is table],
                'rows': rows, 'orphan_cells': orphan_cells, 'fact_links': links,
                'captions': [{'path_from_table': paths[n][len(tp):],
                              'anchor': anchor(n, paths, spans, source), 'text': own_text(n)}
                             for n in table.iterdescendants() if n.tag == XH + 'caption' and nearest(n, {TABLE}) is table],
                'paragraph_refs': {'preceding': [para_ordinals[p] for p in before],
                                   'following': [para_ordinals[p] for p in after],
                                   'attachment': 'document_order_neighbors_not_semantic_evidence'},
                'visibility': visibility(table, paths),
                'grid_expansion': 'not_attempted', 'header_association': 'not_inferred',
                'financial_scope': 'not_inferred'}
        records.append(item)
        public.append({'table_ordinal': ti, 'dom_path': tp, 'anchor': tb,
                       'parent_table_ordinal': table_ordinals.get(parent),
                       'row_count': len(rows), 'cell_count': sum(len(r['cells']) for r in rows),
                       'orphan_cell_count': len(orphan_cells), 'owned_fact_count': len(links),
                       'preceding_paragraph_count': len(before), 'following_paragraph_count': len(after),
                       'markup_hiding_cue': bool(item['visibility']['cues']),
                       'rowspan_attribute_count': sum(c['rowspan']['declared'] is not None for r in rows for c in r['cells']),
                       'colspan_attribute_count': sum(c['colspan']['declared'] is not None for r in rows for c in r['cells'])})
    totals = {'tables': len(tables), 'rows': sum(t['row_count'] for t in public),
              'cells': sum(t['cell_count'] for t in public),
              'orphan_cells': sum(t['orphan_cell_count'] for t in public),
              'nested_tables': sum(t['parent_table_ordinal'] is not None for t in public),
              'facts': len(facts), 'facts_owned_by_tables': sum(t['owned_fact_count'] for t in public),
              'facts_outside_tables': len(owned_facts[None]), 'paragraph_blocks': len(paragraphs),
              'tables_with_markup_hiding_cues': sum(t['markup_hiding_cue'] for t in public),
              'rowspan_attributes': sum(t['rowspan_attribute_count'] for t in public),
              'colspan_attributes': sum(t['colspan_attribute_count'] for t in public),
              'all_element_anchor_locator_pairs_checked': len(paths)}
    if totals['facts_owned_by_tables'] + totals['facts_outside_tables'] != totals['facts']:
        raise ViewError('table_ownership_population_failure')
    return records, {'counts': totals, 'tables': public}

def runtime_bindings():
    files = {str(Path(sys.executable).resolve()): digest(Path(sys.executable).resolve()),
             str(Path(etree.__file__).resolve()): digest(etree.__file__)}
    pyexpat_file = getattr(pyexpat, '__file__', None)
    if pyexpat_file:
        files[str(Path(pyexpat_file).resolve())] = digest(pyexpat_file)
    return {'pyexpat_implementation': 'extension_file' if pyexpat_file else 'built_into_pinned_interpreter',
            'python_version': sys.version, 'lxml_version': list(etree.LXML_VERSION),
            'libxml_version': list(etree.LIBXML_VERSION), 'expat_version': expat.EXPAT_VERSION,
            'binary_files': files}

def load_typed(path):
    facts = []
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            record = json.loads(line)
            if record.get('record_type') == 'fact_pair':
                facts.append(record['typed'])
    return facts

class ExternalWriter:
    def __init__(self, limit=MAX_EXTERNAL_BYTES):
        self.limit, self.bytes = limit, 0
    def write(self, stream, record):
        data = encoded(record)
        if self.bytes + len(data) > self.limit:
            raise OutputLimitExceeded('external_output_byte_limit_exceeded')
        stream.write(data)
        self.bytes += len(data)

def preflight(protocol, source_dir, typed_dir):
    errors = []
    if protocol.get('schema_version') != 'table_view_protocol_v16':
        errors.append('protocol_schema_mismatch')
    if protocol.get('external_output_limit_bytes') != MAX_EXTERNAL_BYTES:
        errors.append('external_output_limit_mismatch')
    if len(protocol.get('sources', [])) != 12:
        errors.append('source_denominator_mismatch')
    required_code = {'scripts/build_table_views_v16.py', 'tests/test_table_views_v16.py',
                     'src/temporal_state/typed_reader_v15_1.py', 'src/temporal_state/__init__.py'}
    required_inputs = {'data/typed_reader_v15/execution_protocol_v15_1.json',
                       'results/typed_reader_execution_v15_1.json', 'docs/table_view_contract_v16.txt'}
    if not required_code.issubset(protocol.get('code_bindings', {})):
        errors.append('required_code_bindings_missing')
    if not required_inputs.issubset(protocol.get('input_bindings', {})):
        errors.append('required_input_bindings_missing')
    for group in ('code_bindings', 'input_bindings'):
        for relative, expected in protocol.get(group, {}).items():
            path = ROOT / relative
            if not path.is_file() or digest(path) != expected:
                errors.append(group + ':' + relative)
    if runtime_bindings() != protocol.get('runtime_bindings'):
        errors.append('runtime_bindings_mismatch')
    if not errors:
        parent = json.loads((ROOT / 'data/typed_reader_v15/execution_protocol_v15_1.json').read_text())
        previous = json.loads((ROOT / 'results/typed_reader_execution_v15_1.json').read_text())
        by_path = {record['source_path']: record['external_records'] for record in previous['records']}
        expected_sources = []
        for item in parent['sources']:
            external = by_path[item['source_path']]
            expected_sources.append(dict(item, typed_filename=Path(external['path']).name,
                                         typed_sha256=external['sha256'], typed_bytes=external['bytes']))
        if protocol['sources'] != expected_sources:
            errors.append('frozen_source_population_or_typed_artifact_mismatch')
    for item in protocol.get('sources', []):
        if any(Path(item[key]).name != item[key] for key in ('external_filename', 'typed_filename')):
            errors.append('external_basename_required:' + item['source_path'])
            continue
        for directory, name, expected_hash, expected_size, kind in (
            (source_dir, item['external_filename'], item['expected_sha256'], item['expected_bytes'], 'source'),
            (typed_dir, item['typed_filename'], item['typed_sha256'], item['typed_bytes'], 'typed')):
            path = directory / name
            if (not path.is_file() or path.stat().st_size != expected_size or digest(path) != expected_hash):
                errors.append(kind + ':' + item['source_path'])
    return errors

def execute(protocol_path, source_dir, typed_dir, external_dir, output):
    if output.exists() or external_dir.exists():
        raise ViewError('refusing_to_overwrite_existing_attempt')
    repository = ROOT.parent.resolve()
    private_path = external_dir.resolve()
    public_path = output.resolve()
    if private_path.is_relative_to(repository):
        raise ViewError('source_bearing_output_must_be_outside_repository')
    if public_path.is_relative_to(private_path):
        raise ViewError('public_inventory_must_be_outside_private_attempt')
    protocol = json.loads(protocol_path.read_text())
    errors = preflight(protocol, source_dir, typed_dir)
    result = {'schema_version': 'table_view_inventory_v16', 'started_at_utc': utc(),
              'protocol_sha256': digest(protocol_path), 'status': 'preflight_failed' if errors else 'running',
              'preflight_errors': errors, 'source_denominator': len(protocol['sources']),
              'expected_nonfraction_denominator': sum(item['expected_nonfraction_count'] for item in protocol['sources']),
              'natural_QA_predictions': 0, 'model_calls': 0, 'question_selection_performed': False,
              'rendering_verified': False, 'independent_parser_scope': 'lxml locators/attributes versus frozen Expat helper; shared helper lineage disclosed',
              'records': [], 'totals': {}}
    if not errors:
        external_dir.mkdir(parents=True)
        (external_dir / 'attempt_started.json').write_bytes(encoded({'started_at_utc': result['started_at_utc'],
            'protocol_sha256': result['protocol_sha256']}))
        writer = ExternalWriter(MAX_EXTERNAL_BYTES - PRIVATE_RESERVE)
        aggregate = Counter()
        for item in protocol['sources']:
            local = external_dir / (Path(item['external_filename']).stem + '.tables.jsonl')
            entry = {'source_path': item['source_path'], 'source_sha256': item['expected_sha256'],
                     'source_bytes': item['expected_bytes'],
                     'expected_nonfraction_count': item['expected_nonfraction_count'], 'status': 'running'}
            try:
                source = (source_dir / item['external_filename']).read_bytes()
                typed = load_typed(typed_dir / item['typed_filename'])
                if len(typed) != item['expected_nonfraction_count']:
                    raise ViewError('frozen_nonfraction_count_mismatch')
                records, summary = extract_views(source, typed, source_path=item['source_path'],
                                                source_sha256=item['expected_sha256'])
                with local.open('xb') as stream:
                    for record in records:
                        writer.write(stream, record)
                entry.update(summary)
                entry['status'] = 'completed'
                aggregate.update(summary['counts'])
                del source, typed, records, summary
            except Exception as exc:
                entry['status'] = 'failed'
                entry['error_class'] = type(exc).__name__
                # Public reasons are static codes; raw third-party text is never copied.
                entry['failure_code'] = str(exc) if isinstance(exc, (ViewError, OutputLimitExceeded)) else 'technical_exception'
            finally:
                if local.exists():
                    entry['external_records'] = {'filename': local.name, 'bytes': local.stat().st_size,
                                                 'sha256': digest(local), 'complete': entry['status'] == 'completed'}
                result['records'].append(entry)
                gc.collect()
        result['totals'] = dict(aggregate)
        result['status'] = 'completed' if all(r['status'] == 'completed' for r in result['records']) else 'failed_sources_retained'
        result['external_payload_bytes'] = writer.bytes
        result['external_output_limit_bytes'] = MAX_EXTERNAL_BYTES
        (external_dir / 'attempt_finished.json').write_bytes(encoded({'status': result['status'], 'finished_at_utc': utc(),
            'completed_sources': sum(r['status'] == 'completed' for r in result['records'])}))
        result['external_total_bytes'] = sum(p.stat().st_size for p in external_dir.iterdir() if p.is_file())
        if result['external_total_bytes'] > MAX_EXTERNAL_BYTES:
            result['status'] = 'external_limit_violation'
    result['finished_at_utc'] = utc()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'source_denominator', 'totals')}, sort_keys=True))
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--typed-records', type=Path, required=True)
    parser.add_argument('--external-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = execute(args.protocol, args.sources, args.typed_records, args.external_output, args.output)
    raise SystemExit(0 if result['status'] == 'completed' else 1)

if __name__ == '__main__':
    main()

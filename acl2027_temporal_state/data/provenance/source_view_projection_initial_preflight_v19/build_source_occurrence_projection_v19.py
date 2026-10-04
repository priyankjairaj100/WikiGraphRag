#!/usr/bin/env python3
"""Bounded source-only occurrence projection; no numeric decoding or semantics."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.parsers.expat as expat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from temporal_state import typed_reader_v15_1 as byte_reader

ANCHOR_KEYS = ('byte_start', 'byte_stop', 'span_sha256')
FORBIDDEN = {'normalized_value', 'numeric_status', 'binding_status', 'declared_aspects',
             'reported_aspects', 'context_id', 'unit_id', 'contextRef', 'unitRef', 'scale',
             'sign', 'format', 'concept', 'concept_qname', 'attrs', 'raw_xml', 'issues',
             'visible_row_label_candidates', 'population_binding', 'source_fact_ordinal',
             'question_free_registry', 'interface_shape_candidate', 'lexical_text'}


class ProjectionError(ValueError):
    """Unsafe, inconsistent, or outside the frozen projection profile."""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def digest(path):
    return sha(Path(path).read_bytes())


def anchor_at(source, start, stop):
    return {'byte_start': start, 'byte_stop': stop, 'span_sha256': sha(source[start:stop])}


def checked_anchor(source, anchor):
    if not isinstance(anchor, dict):
        raise ProjectionError('anchor_not_object')
    start, stop = anchor.get('byte_start'), anchor.get('byte_stop')
    if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= len(source):
        raise ProjectionError('anchor_bounds')
    actual = anchor_at(source, start, stop)
    if anchor.get('span_sha256') != actual['span_sha256']:
        raise ProjectionError('anchor_digest')
    return actual


def xml_text(text, *, cdata=False):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if cdata:
        return text
    named = {'lt':'<', 'gt':'>', 'amp':'&', 'apos':"'", 'quot':'"'}
    def replace(match):
        value = match.group(1)
        if value in named:
            return named[value]
        if re.fullmatch(r'#[0-9]+', value):
            code = int(value[1:])
        elif re.fullmatch(r'#x[0-9a-fA-F]+', value):
            code = int(value[2:], 16)
        else:
            raise ProjectionError('unsupported_entity')
        if not (code in (9, 10, 13) or 0x20 <= code <= 0xD7FF or
                0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF):
            raise ProjectionError('invalid_xml_character')
        return chr(code)
    # Detect malformed ampersands before replacement so &amp;amp; decodes once.
    if re.search(r'&(?![^&;<>\s]+;)', text):
        raise ProjectionError('malformed_entity')
    return re.sub(r'&([^&;<>\s]+);', replace, text)


def extract_text_segments(source: bytes, anchor: dict):
    """Return only text-node bytes and XML-decoded text, excluding all markup."""
    checked = checked_anchor(source, anchor)
    start, stop = checked['byte_start'], checked['byte_stop']
    fragment = source[start:stop]
    # A separate namespace-unaware parser validates this exact single element.
    # Prefix definitions live outside the span, so they are deliberately not inferred.
    parser = expat.ParserCreate()
    decoded = []
    def reject(*args):
        raise ProjectionError('unsafe_xml_declaration')
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.CharacterDataHandler = decoded.append
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(fragment, True)
    except (expat.ExpatError, UnicodeError) as exc:
        raise ProjectionError('invalid_element_span') from exc
    segments = []
    pos = start
    def append(begin, end, kind):
        if begin == end:
            return
        try:
            raw_text = source[begin:end].decode('utf-8')
        except UnicodeError as exc:
            raise ProjectionError('invalid_utf8_segment') from exc
        segments.append({'kind':kind, 'anchor':anchor_at(source, begin, end),
                         'raw_text':raw_text, 'decoded_text':xml_text(raw_text, cdata=kind == 'cdata')})
    while pos < stop:
        if source.startswith(b'<!--', pos):
            end = source.find(b'-->', pos + 4, stop)
            if end < 0: raise ProjectionError('unterminated_comment')
            pos = end + 3
        elif source.startswith(b'<?', pos):
            end = source.find(b'?>', pos + 2, stop)
            if end < 0: raise ProjectionError('unterminated_pi')
            pos = end + 2
        elif source.startswith(b'<![CDATA[', pos):
            end = source.find(b']]>', pos + 9, stop)
            if end < 0: raise ProjectionError('unterminated_cdata')
            append(pos + 9, end, 'cdata')
            pos = end + 3
        elif source.startswith(b'<!', pos):
            raise ProjectionError('unsupported_declaration')
        elif source[pos:pos + 1] == b'<':
            try:
                pos = byte_reader._tag_end(source, pos)
            except byte_reader.ReaderError as exc:
                raise ProjectionError('unterminated_tag') from exc
            if pos > stop: raise ProjectionError('tag_outside_anchor')
        else:
            end = source.find(b'<', pos, stop)
            if end < 0: end = stop
            append(pos, end, 'text')
            pos = end
    joined = ''.join(s['decoded_text'] for s in segments)
    if joined != ''.join(decoded):
        raise ProjectionError('independent_text_decoder_mismatch')
    return {'segments':segments, 'decoded_text':joined}


def inside(inner, outer):
    return outer['byte_start'] <= inner['byte_start'] and inner['byte_stop'] <= outer['byte_stop']


def same_anchor(left, right):
    return all(left.get(k) == right.get(k) for k in ANCHOR_KEYS)


def nearest(node, names):
    for ancestor in node.ancestors():
        if ancestor.tag in {byte_reader._tag(byte_reader.XHTML, name) for name in names}:
            return ancestor
    return None


def node_locator(node, source):
    return {'dom_path':node.path, 'anchor':anchor_at(source, node.start, node.stop)}


def bind_cell(node, table_views: dict, source: bytes, blocks: list):
    """Join only the frozen nearest-table fact link; containment is insufficient."""
    result = {'status':'unresolved', 'reason':None, 'table':None, 'row':None, 'cell':None,
              'row_cell_index':None, 'containing_block_handles':[],
              'table_handle':None, 'row_handle':None, 'cell_handle':None}
    table, row, cell = nearest(node, ('table',)), nearest(node, ('tr',)), nearest(node, ('td','th'))
    if cell is None and table is None:
        result.update(status='not_in_table_cell', reason='no_source_table_cell_ancestor')
        return result
    if table is None or row is None or cell is None or nearest(cell, ('table',)) is not table or nearest(row, ('table',)) is not table:
        result['reason'] = 'incomplete_or_cross_table_ancestry'
        return result
    view = table_views.get(table.path)
    if view is None:
        result['reason'] = 'nearest_table_view_missing'
        return result
    fact_anchor = anchor_at(source, node.start, node.stop)
    links = [link for link in view.get('fact_links', [])
             if table.path + link.get('path_from_table','') == node.path and same_anchor(link.get('anchor',{}), fact_anchor)]
    if len(links) != 1:
        result['reason'] = 'nearest_table_fact_link_not_unique'
        return result
    index = links[0].get('row_cell_index')
    if not isinstance(index, list) or len(index) != 2 or any(type(i) is not int or i < 0 for i in index):
        result['reason'] = 'frozen_row_cell_index_missing'
        return result
    try:
        row_view = view['rows'][index[0]]
        cell_view = row_view['cells'][index[1]]
        expected_row = table.path + row_view['path_from_table']
        expected_cell = expected_row + cell_view['path_from_row']
        exact = (view['dom_path'] == table.path and same_anchor(view['anchor'], anchor_at(source, table.start, table.stop))
                 and expected_row == row.path and expected_cell == cell.path
                 and same_anchor(row_view['anchor'], anchor_at(source, row.start, row.stop))
                 and same_anchor(cell_view['anchor'], anchor_at(source, cell.start, cell.stop)))
    except (KeyError, IndexError, TypeError):
        exact = False
    if not exact:
        result['reason'] = 'frozen_row_cell_locator_mismatch'
        return result
    owners = ['b' + str(i).zfill(3) for i,block in enumerate(blocks)
              if inside(anchor_at(source, cell.start, cell.stop), block['anchor'])]
    if not owners:
        result['reason'] = 'cell_not_wholly_in_fixed_context'
        return result
    table_rank = sorted(table_views, key=lambda path: table_views[path]['anchor']['byte_start']).index(table.path)
    table_handle = 't' + str(table_rank).zfill(4)
    row_handle = table_handle + 'r' + str(index[0]).zfill(4)
    cell_handle = row_handle + 'c' + str(index[1]).zfill(4)
    result.update(status='bound_to_frozen_nearest_table_cell', reason=None,
                  table_handle=table_handle, row_handle=row_handle, cell_handle=cell_handle,
                  table=node_locator(table,source), row=node_locator(row,source), cell=node_locator(cell,source),
                  row_cell_index=index, containing_block_handles=owners)
    return result


def recognized_visibility_cues(node):
    cues = []
    for ancestor in [node, *node.ancestors()]:
        if ancestor.tag == byte_reader._tag(byte_reader.IX, 'hidden'):
            cues.append('inline_hidden_container')
        if 'hidden' in ancestor.attrs:
            cues.append('html_hidden_attribute')
        style = ancestor.attrs.get('style','')
        if re.search(r'(?:^|;)\s*display\s*:\s*none\s*(?:!important\s*)?(?:;|$)', style, re.I):
            cues.append('inline_display_none')
        if re.search(r'(?:^|;)\s*visibility\s*:\s*(?:hidden|collapse)\s*(?:!important\s*)?(?:;|$)', style, re.I):
            cues.append('inline_visibility_hidden_or_collapse')
    return sorted(set(cues))


def project_occurrence(source, entry, node_by_path, table_views, blocks, handle):
    """Privileged join in, whitelisted lexical/locator record out; retain failures."""
    result = {'occurrence_handle':handle, 'source_sha256':sha(source), 'locator_status':'unresolved',
              'unresolved_reason':None, 'locator':None, 'containing_block_handles':[],
              'source_text':None, 'cell_binding':None,
              'visibility':{'status':'unverified', 'computed_css_evaluated':False,
                            'occurrence_visually_reviewed':False, 'recognized_cue_kinds':[]}}
    try:
        claimed = entry.get('anchor',{})
        checked = checked_anchor(source, claimed)
        if claimed.get('source_sha256') != sha(source):
            raise ProjectionError('occurrence_source_digest_mismatch')
        node = node_by_path.get(claimed.get('dom_path'))
        if node is None or node.tag != byte_reader._tag(byte_reader.IX, 'nonFraction'):
            raise ProjectionError('occurrence_node_missing_or_wrong_element')
        if not same_anchor(checked, anchor_at(source,node.start,node.stop)):
            raise ProjectionError('occurrence_node_anchor_mismatch')
        owners = [i for i,b in enumerate(blocks) if inside(checked,b['anchor'])]
        if not owners:
            raise ProjectionError('occurrence_outside_fixed_context')
        if entry.get('block_ids') != [blocks[i]['block_id'] for i in owners]:
            raise ProjectionError('frozen_block_membership_mismatch')
        source_text = extract_text_segments(source, checked)
        if source_text['decoded_text'] != node.text():
            raise ProjectionError('source_tree_text_mismatch')
        result.update(locator_status='bound_to_exact_source_node', locator=node_locator(node,source),
                      containing_block_handles=['b' + str(i).zfill(3) for i in owners], source_text=source_text,
                      cell_binding=bind_cell(node,table_views,source,blocks))
        result['visibility']['recognized_cue_kinds'] = recognized_visibility_cues(node)
    except ProjectionError as exc:
        result['unresolved_reason'] = str(exc)
    result['entry_sha256'] = sha(encoded(result))
    return result


def forbid_helpers(value):
    if isinstance(value, dict):
        if FORBIDDEN.intersection(value):
            raise ProjectionError('forbidden_helper_key')
        for item in value.values(): forbid_helpers(item)
    elif isinstance(value, list):
        for item in value: forbid_helpers(item)


def require_keys(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ProjectionError('allowlist_' + name)


def validate_author_context(author):
    require_keys(author, ('schema_version','bundle_id','source_id','source_sha256','author_release_allowed',
                         'identity_card','status','blocks'), 'author')
    if (author['schema_version'] != 'source_only_author_staging_v17' or author['author_release_allowed'] is not False
        or author['identity_card'] is not None or not isinstance(author['blocks'],list)
        or not isinstance(author['bundle_id'],str) or not re.fullmatch(r's[0-9]{2}',author['source_id'])
        or not re.fullmatch('[a-f0-9]{64}',author['source_sha256'])
        or author['status'] != 'pending_visible_identity_and_complete_bundle_admission'):
        raise ProjectionError('author_context_not_unreleased_parent')
    seen = set()
    for block in author['blocks']:
        require_keys(block, ('block_id','kind','dom_path','anchor','text','rows'), 'block')
        require_keys(block['anchor'], ANCHOR_KEYS, 'anchor')
        if (not isinstance(block['block_id'],str) or block['block_id'] in seen
            or not isinstance(block['text'],str) or not isinstance(block['dom_path'],str)
            or block['kind'] not in ('table','paragraph','table_row') or not isinstance(block['rows'],list)
            or type(block['anchor']['byte_start']) is not int or type(block['anchor']['byte_stop']) is not int
            or not isinstance(block['anchor']['span_sha256'],str)):
            raise ProjectionError('duplicate_block_or_nontext')
        seen.add(block['block_id'])
        for row in block['rows']:
            require_keys(row, ('text','cells'), 'row')
            if not isinstance(row['text'],str) or not isinstance(row['cells'],list):
                raise ProjectionError('row_leaf_type')
            for cell in row['cells']:
                require_keys(cell, ('text','rowspan','colspan'), 'cell')
                if not isinstance(cell['text'],str):raise ProjectionError('cell_text_leaf_type')
                for span in ('rowspan','colspan'):
                    require_keys(cell[span], ('declared','parsed','status'), 'span')
                    value=cell[span]
                    if (value['declared'] is not None and not isinstance(value['declared'],str)
                        or value['parsed'] is not None and type(value['parsed']) is not int
                        or not isinstance(value['status'],str)):
                        raise ProjectionError('span_leaf_type')
    forbid_helpers(author)


def project_bundle(source, private, table_views, exported_author, identity_card_reference):
    author = private['author_staging']
    validate_author_context(author)
    if identity_card_reference is not None:
        require_keys(identity_card_reference, ('source_handle','path','bytes','sha256'), 'identity_reference')
        if (identity_card_reference['source_handle'] != author['source_id']
            or not isinstance(identity_card_reference['path'],str)
            or type(identity_card_reference['bytes']) is not int or identity_card_reference['bytes'] <= 0
            or not re.fullmatch('[a-f0-9]{64}',identity_card_reference['sha256'])):
            raise ProjectionError('invalid_identity_reference')
    if encoded(author) != encoded(exported_author) or author['source_sha256'] != sha(source):
        raise ProjectionError('whole_author_context_or_source_mismatch')
    nodes = byte_reader._parse(source)
    node_by_path = {node.path:node for node in nodes}
    for block in author['blocks']:
        checked_anchor(source,block['anchor'])
        node = node_by_path.get(block['dom_path'])
        if node is None or not same_anchor(block['anchor'],anchor_at(source,node.start,node.stop)):
            raise ProjectionError('whole_block_locator_mismatch')
    entries = private['question_free_registry']['facts']
    result = {'schema_version':'source_occurrence_projection_v19', 'bundle_handle':author['bundle_id'],
              'source_sha256':sha(source), 'author_release_allowed':False, 'question_free':True,
              'semantic_mapping_performed':False, 'whole_source_context':deepcopy(author),
              'block_handles':[{'block_handle':'b' + str(i).zfill(3), 'block_id':b['block_id']}
                               for i,b in enumerate(author['blocks'])],
              'separate_identity_card_reference':deepcopy(identity_card_reference),
              'occurrences':[project_occurrence(source,e,node_by_path,table_views,author['blocks'],
                                               'o' + str(i).zfill(4)) for i,e in enumerate(entries)]}
    forbid_helpers(result)
    if len(result['occurrences']) != len(entries):
        raise ProjectionError('occurrence_retention_failure')
    return result

CODE = ('scripts/build_source_occurrence_projection_v19.py',
        'tests/test_source_occurrence_projection_v19.py',
        'tests/test_source_occurrence_projection_adversarial_v19.py',
        'docs/source_occurrence_projection_contract_v19.txt',
        'src/temporal_state/typed_reader_v15_1.py',
        'scripts/preflight_source_view_projection_v19.py')
INPUTS = ('data/reader_binding_v18/pre_author_bundle_protocol.json',
          'results/pre_author_bundle_inventory_v18.json',
          'results/pre_author_bundle_shape_terminal_v18.json',
          'results/pre_author_projection_export_v17.json',
          'data/reader_binding_v16/table_view_protocol_v16_1.json',
          'results/table_view_inventory_v16_1.json',
          'results/source_view_availability_summary_v18_1.json',
          'results/source_view_availability_summary_v17_2.json',
          'results/source_identity_card_admission_v18.json',
          'docs/source_occurrence_annotation_plan_v19.txt')
RASTER_RECEIPTS = {
    'results/source_render_admission_00_05_v17.json':'results/source_render_natural_execution_v17.json',
    'results/source_render_admission_root_06_11_v17.json':'results/source_render_natural_execution_v17.json',
    'results/source_render_recovery_admission_root_v17_1.json':'results/source_render_telemetry_recovery_execution_v17_1.json',
    'results/source_render_rgba_recovery_admission_root_v17_2.json':'results/source_render_rgba_recovery_execution_v17_2.json',
    'results/source_render_dependencies_admission_00_05_v18.json':'results/source_render_dependencies_execution_v18.json',
    'results/source_render_dependencies_admission_root_v18.json':'results/source_render_dependencies_execution_v18.json',
    'results/source_render_nflx_dependency_recovery_admission_v18_1.json':'results/source_render_nflx_dependency_recovery_execution_v18_1.json'}
INPUTS += tuple(RASTER_RECEIPTS) + tuple(dict.fromkeys(RASTER_RECEIPTS.values()))
INPUTS += ('results/source_view_projection_preflight_v19.json',)
REVIEW = 'results/source_occurrence_projection_independent_review_v19.json'
OUTPUT = 'results/source_occurrence_projection_inventory_v19.json'
EXTERNAL = '/dev/shm/wikigraph_v15/external/source_occurrence_projection_attempt01_v19'
SOURCES = '/dev/shm/wikigraph_v15/external/fresh_sources'
TABLES = '/dev/shm/wikigraph_v15/external/table_views_attempt02'
MAX_EXTERNAL = 25_000_000
MAX_SOURCE = 25_000_000
MAX_TABLE_STREAM = 100_000_000


def read_json(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    path = Path(path)
    with path.open('xb') as stream:
        stream.write(encoded(value))


def metadata_population():
    inventory, export, sources, tables, cards = [read_json(ROOT/INPUTS[i]) for i in (1,3,4,5,8)]
    records = inventory.get('records',[])
    if (inventory.get('schema_version') != 'preauthor_bundle_inventory_v18' or len(records) != 21
        or inventory.get('materialized_bundles') != 21 or inventory.get('author_release_allowed') is not False
        or inventory.get('protocol_sha256') != digest(ROOT/INPUTS[0])
        or sum(r['numeric_occurrences'] for r in records) != 326
        or len({r['bundle_id'] for r in records}) != 21):
        raise ProjectionError('fixed_parent_population_mismatch')
    if ([r['bundle_id'] for r in export.get('artifacts',[])] != [r['bundle_id'] for r in records]
        or inventory['parent_projection_export_sha256'] != digest(ROOT/INPUTS[3])):
        raise ProjectionError('fixed_export_population_mismatch')
    if (len(sources.get('sources',[])) != 12 or len(tables.get('records',[])) != 12
        or tables.get('protocol_sha256') != digest(ROOT/INPUTS[4])
        or [(s['source_path'],s['expected_sha256']) for s in sources['sources']] !=
           [(r['source_path'],r['source_sha256']) for r in tables['records']]):
        raise ProjectionError('fixed_source_table_population_mismatch')
    if (cards.get('cards_admitted') != 12 or len(cards.get('records',[])) != 12
        or cards.get('author_release') is not False
        or [r['source_id'] for r in cards['records']] != ['s'+str(i).zfill(2) for i in range(12)]):
        raise ProjectionError('fixed_card_population_mismatch')
    for record in records:
        index = record['source_index']
        if type(index) is not int or not 0 <= index < 12:
            raise ProjectionError('source_index_outside_fixed_population')
        source = sources['sources'][index]
        if (record['source_path'],record['source_sha256']) != (source['source_path'],source['expected_sha256']):
            raise ProjectionError('bundle_source_binding_mismatch')
    return inventory,export,sources,tables,cards


def protocol_value(*, draft=False):
    code = {p:digest(ROOT/p) for p in CODE}
    if not draft:
        review = read_json(ROOT/REVIEW)
        if (review.get('status') != 'passed_authored_review' or review.get('open_blockers') != []
            or review.get('code_bindings') != code):
            raise ProjectionError('independent_review_not_closed_or_exact')
    inventory,export,sources,tables,cards = metadata_population()
    return {'schema_version':'source_occurrence_projection_protocol_v19',
            'status':'draft_not_executable' if draft else 'frozen_after_independent_review',
            'code_bindings':code, 'input_bindings':{p:digest(ROOT/p) for p in INPUTS},
            'review_path':REVIEW, 'review_sha256':None if draft else digest(ROOT/REVIEW),
            'runtime':{'python':sys.version, 'executable_sha256':digest(sys.executable), 'expat':expat.EXPAT_VERSION},
            'planned_bundles':21, 'planned_occurrence_entries':326, 'source_denominator':12,
            'separate_identity_cards':12, 'external_bytes_limit':MAX_EXTERNAL,
            'source_bytes_limit_each':MAX_SOURCE, 'table_stream_bytes_limit_each':MAX_TABLE_STREAM,
            'source_directory':SOURCES, 'table_directory':TABLES,
            'external_output':EXTERNAL, 'output':OUTPUT,
            'author_release_allowed':False, 'question_free':True,
            'source_population':[{'source_handle':'s'+str(i).zfill(2), 'sha256':s['expected_sha256'],
                                  'bytes':s['expected_bytes']} for i,s in enumerate(sources['sources'])],
            'bundle_population':[{'bundle_handle':r['bundle_id'], 'source_handle':'s'+str(r['source_index']).zfill(2),
                                  'occurrence_entries':r['numeric_occurrences'],
                                  'parent_bundle_sha256':r['external_artifact']['sha256'],
                                  'parent_projection_sha256':e['sha256']}
                                 for r,e in zip(inventory['records'],export['artifacts'])],
            'policy':'all_fixed_entries_no_selection_no_semantic_inference_unresolved_retained',
            'whole_context_policy':'exact_immutable_v17_author_projection_from_v18_parent',
            'visibility_policy':'whole_views_do_not_certify_occurrence_visibility',
            'attempt_policy':'one_new_directory_no_overwrite_no_retry_preserve_partial_failure'}


def checked_file(path, binding, *, filename=False):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size != binding['bytes'] or digest(path) != binding['sha256']:
        raise ProjectionError('external_file_binding_mismatch')
    return path


def directory_member(directory, filename):
    if Path(filename).name != filename:
        raise ProjectionError('external_filename_not_basename')
    return Path(directory)/filename


def load_tables(record):
    binding = record['external_records']
    if type(record['storage']['uncompressed_bytes']) is not int or not 0 <= record['storage']['uncompressed_bytes'] <= MAX_TABLE_STREAM:
        raise ProjectionError('declared_expanded_table_stream_limit')
    path = checked_file(directory_member(TABLES,binding['filename']),binding)
    table_map = {}; stream_hash = hashlib.sha256(); stream_bytes = 0
    with gzip.open(path,'rb') as stream:
        while True:
            line = stream.readline(MAX_TABLE_STREAM - stream_bytes + 1)
            if not line:break
            stream_bytes += len(line)
            if stream_bytes > MAX_TABLE_STREAM: raise ProjectionError('expanded_table_stream_limit')
            stream_hash.update(line); item=json.loads(line)
            if item.get('record_type') == 'table':
                if item['dom_path'] in table_map: raise ProjectionError('duplicate_frozen_table_path')
                table_map[item['dom_path']] = item
    if (stream_bytes != record['storage']['uncompressed_bytes'] or
        stream_hash.hexdigest() != record['storage']['uncompressed_sha256']):
        raise ProjectionError('expanded_table_stream_binding_mismatch')
    return table_map


def artifact_projection(artifact):
    require_keys(artifact, ('path','bytes','sha256'), 'raster_artifact')
    if (not isinstance(artifact['path'],str) or not Path(artifact['path']).is_absolute()
        or type(artifact['bytes']) is not int or artifact['bytes'] <= 0
        or not isinstance(artifact['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',artifact['sha256'])):
        raise ProjectionError('invalid_raster_artifact_binding')
    return {key:artifact[key] for key in ('path','bytes','sha256')}


def exact_receipt_block(receipt, source_sha256, block):
    found=[]
    for source in receipt.get('sources',[]):
        if source.get('source_sha256') == source_sha256:
            found.extend(item for item in source.get('blocks',[]) if item.get('block_id') == block['block_id'])
    if (len(found) != 1 or found[0].get('dom_path') != block['dom_path']
        or not same_anchor(found[0].get('anchor',{}),block['anchor'])):
        raise ProjectionError('raster_source_block_identity_mismatch')
    return found[0]


def project_qualified_view(source_sha256, block, block_handle, admission, execution,
                           admission_binding, execution_binding):
    """Rebuild only qualified PDF/PNG references; never copy receipt annotations."""
    for binding in (admission_binding,execution_binding):
        require_keys(binding, ('path','sha256'), 'raster_receipt_reference')
        if (not isinstance(binding['path'],str) or not isinstance(binding['sha256'],str)
            or not re.fullmatch('[a-f0-9]{64}',binding['sha256'])):
            raise ProjectionError('invalid_raster_receipt_reference')
    admitted=exact_receipt_block(admission,source_sha256,block)
    rendered=exact_receipt_block(execution,source_sha256,block)
    count=admitted.get('raster_pages_available')
    if (admitted.get('decision') != 'admitted_as_qualified_inspection_view'
        or type(count) is not int or count < 1
        or admitted.get('raster_page_indices_inspected') != list(range(count))
        or rendered.get('pages') != count or len(rendered.get('pngs',[])) != count):
        raise ProjectionError('raster_not_qualified_complete_page_set')
    pdf=artifact_projection(rendered.get('pdf'))
    pngs=[artifact_projection(artifact) for artifact in rendered['pngs']]
    if (not pdf['path'].endswith('/rendered/source-block.pdf') or
        [Path(p['path']).name for p in pngs] != ['page-'+str(i+1)+'.png' for i in range(count)]
        or len({p['path'] for p in pngs}) != count):
        raise ProjectionError('raster_path_or_order_mismatch')
    assembly=rendered.get('source_assembly_sha256')
    if not isinstance(assembly,str) or not re.fullmatch('[a-f0-9]{64}',assembly):
        raise ProjectionError('raster_assembly_digest_missing')
    if 'reviewed_artifacts' in admitted:
        reviewed=admitted['reviewed_artifacts']
        for item in reviewed:
            require_keys(item, ('filename','bytes','sha256'), 'admitted_artifact')
        def matches(artifact):
            return [item for item in reviewed if isinstance(item['filename'],str)
                    and artifact['path'].endswith('/'+item['filename'])
                    and item['bytes'] == artifact['bytes'] and item['sha256'] == artifact['sha256']]
        if any(len(matches(artifact)) != 1 for artifact in [pdf,*pngs]):
            raise ProjectionError('raster_not_bound_in_admission')
        fragments=[r for r in reviewed if r['filename'].endswith('source-fragment.xml')]
        assemblies=[r for r in reviewed if r['filename'].endswith('source-assembly.html')]
        if (len(fragments) != 1 or fragments[0]['sha256'] != block['anchor']['span_sha256']
            or len(assemblies) != 1 or assemblies[0]['sha256'] != assembly):
            raise ProjectionError('raster_admitted_source_fragment_or_assembly_mismatch')
        if len([r for r in reviewed if r['filename'].endswith('.png')]) != count:
            raise ProjectionError('raster_admitted_page_population_mismatch')
    else:
        artifacts=admitted.get('artifact_bindings',{})
        admitted_pdf=artifact_projection(artifacts.get('rendered/source-block.pdf'))
        admitted_pngs=[artifact_projection(a) for a in admitted.get('raster_bindings',[])]
        fragment=artifact_projection(artifacts.get('source-fragment.xml'))
        source_assembly=artifact_projection(artifacts.get('source-assembly.html'))
        if (pdf != admitted_pdf or pngs != admitted_pngs
            or fragment['sha256'] != block['anchor']['span_sha256'] or source_assembly['sha256'] != assembly):
            raise ProjectionError('raster_admission_artifact_mismatch')
    result={'block_handle':block_handle,'status':'qualified_inspection_view_only',
            'admission_receipt':{k:admission_binding[k] for k in ('path','sha256')},
            'render_receipt':{k:execution_binding[k] for k in ('path','sha256')},
            'source_assembly_sha256':assembly, 'pdf':pdf,
            'pages':[dict(page_index=i,**artifact) for i,artifact in enumerate(pngs)]}
    forbid_helpers(result)
    return result


def whole_view_checks(source_sha256, blocks, old_views, new_views, protocol):
    lineage = old_views['original_population_block_lineage'] + new_views['dependency_block_lineage']
    out = []
    for i,block in enumerate(blocks):
        found = [v for v in lineage if v['source_sha256'] == source_sha256 and v['block_id'] == block['block_id']]
        if len(found) != 1:
            raise ProjectionError('whole_view_lineage_not_unique')
        record = found[0]
        decision = record.get('derived_available_decision',record.get('derived_decision'))
        if (decision != 'admitted_as_qualified_inspection_view' or record['dom_path'] != block['dom_path']
            or not same_anchor(record['anchor'],block['anchor'])):
            raise ProjectionError('whole_view_lineage_mismatch')
        admission_path = record.get('separate_recovery_receipt') or record.get('admission_receipt') or record.get('original_receipt')
        if admission_path not in RASTER_RECEIPTS:raise ProjectionError('unbound_raster_admission_receipt')
        execution_path = RASTER_RECEIPTS[admission_path]
        view = project_qualified_view(source_sha256,block,'b'+str(i).zfill(3),
                                      read_json(ROOT/admission_path),read_json(ROOT/execution_path),
                                      {'path':admission_path,'sha256':protocol['input_bindings'][admission_path]},
                                      {'path':execution_path,'sha256':protocol['input_bindings'][execution_path]})
        for artifact in [view['pdf'], *view['pages']]:
            checked_file(artifact['path'],artifact)
        out.append(view)
    return out


def write_packet(external, filename, raw, previous_bytes):
    if previous_bytes + len(raw) > MAX_EXTERNAL:
        raise ProjectionError('external_output_limit')
    path = directory_member(external,filename)
    with path.open('xb') as stream:
        stream.write(raw)
    return previous_bytes + len(raw)


def build(protocol_path):
    protocol = read_json(protocol_path)
    if protocol.get('status') != 'frozen_after_independent_review' or protocol != protocol_value():
        raise ProjectionError('protocol_not_exact_frozen_reviewed_recipe')
    output=ROOT/OUTPUT; external=Path(EXTERNAL)
    if output.exists() or output.with_suffix('.failure.json').exists() or external.exists():
        raise ProjectionError('attempt_or_output_already_exists')
    if ROOT.parent.resolve() in external.resolve().parents:
        raise ProjectionError('private_output_inside_repository')
    inventory,export,sources,tables,cards=metadata_population()
    old_views,new_views=read_json(ROOT/INPUTS[7]),read_json(ROOT/INPUTS[6])
    external.mkdir(parents=True,exist_ok=False)
    records=[];total_bytes=0;total_entries=0;stage='initializing'; loaded_index=None
    try:
        for parent,exported in zip(inventory['records'],export['artifacts']):
            stage='parent_bindings';index=parent['source_index'];source=sources['sources'][index]
            if loaded_index != index:
                if source['expected_bytes'] > MAX_SOURCE: raise ProjectionError('source_bytes_limit')
                source_path=directory_member(SOURCES,source['external_filename'])
                checked_file(source_path,{'bytes':source['expected_bytes'],'sha256':source['expected_sha256']})
                source_bytes=source_path.read_bytes();table_map=load_tables(tables['records'][index]);loaded_index=index
            artifact=parent['external_artifact']
            private_path=checked_file(directory_member(inventory['external_directory'],artifact['filename']),artifact)
            author_path=checked_file(directory_member(export['external_directory'],exported['filename']),exported)
            card_record=cards['records'][index];card=card_record['admitted_card']
            checked_file(card['path'],card)
            identity_ref={'source_handle':card_record['source_id'],'path':card['path'],'bytes':card['bytes'],'sha256':card['sha256']}
            private=read_json(private_path);author=read_json(author_path)
            if (private['author_staging']['bundle_id'] != parent['bundle_id'] or
                len(private['question_free_registry']['facts']) != parent['numeric_occurrences']):
                raise ProjectionError('parent_entry_population_mismatch')
            stage='source_projection'
            packet=project_bundle(source_bytes,private,table_map,author,identity_ref)
            packet['whole_view_availability']=whole_view_checks(source['expected_sha256'],author['blocks'],old_views,new_views,protocol)
            packet['whole_view_receipt_bindings']={p:protocol['input_bindings'][p] for p in (INPUTS[6],INPUTS[7])}
            forbid_helpers(packet)
            if encoded(packet['whole_source_context']) != encoded(author):
                raise ProjectionError('whole_projection_changed')
            raw=encoded(packet)
            filename=parent['bundle_id']+'.occurrence_projection.json'
            total_bytes=write_packet(external,filename,raw,total_bytes)
            total_entries+=len(packet['occurrences'])
            records.append({'bundle_handle':parent['bundle_id'], 'source_handle':'s'+str(index).zfill(2),
                            'source_sha256':source['expected_sha256'], 'occurrence_entries':len(packet['occurrences']),
                            'locator_status_counts':dict(Counter(o['locator_status'] for o in packet['occurrences'])),
                            'cell_status_counts':dict(Counter(o['cell_binding']['status'] if o['cell_binding'] else 'unresolved_occurrence'
                                                             for o in packet['occurrences'])),
                            'whole_context_exact':True, 'whole_block_count':len(author['blocks']),
                            'external_artifact':{'filename':filename,'bytes':len(raw),'sha256':sha(raw)},
                            'parent_bundle_sha256':artifact['sha256'],'parent_projection_sha256':exported['sha256']})
        stage='terminal_accounting'
        if len(records) != 21 or total_entries != 326:raise ProjectionError('terminal_fixed_denominator_mismatch')
        # Inputs and code must still equal the recipe after every output was written.
        if protocol != protocol_value():raise ProjectionError('frozen_recipe_changed_during_attempt')
        result={'schema_version':'source_occurrence_projection_inventory_v19','status':'materialized_source_only_not_semantically_admitted',
                'protocol_sha256':digest(protocol_path),'planned_bundles':21,'materialized_bundles':len(records),
                'planned_occurrence_entries':326,'retained_occurrence_entries':total_entries,'records':records,
                'external_directory':EXTERNAL,'external_bytes':total_bytes,'source_population':12,'separate_identity_cards':12,
                'whole_context_exact_count':21,'author_release_allowed':False,'semantic_mappings_created':0,
                'questions_authored':0,'reference_answers_authored':0,'natural_model_calls':0,
                'visibility_claim':'all_occurrence_visibility_unverified','deduplication_performed':False}
        write_new(output,result)
        return result
    except Exception as exc:
        failure={'schema_version':'source_occurrence_projection_failure_v19','status':'failed_no_retry',
                 'protocol_sha256':digest(protocol_path),'completed_bundles':len(records),'completed_occurrence_entries':total_entries,
                 'external_directory':EXTERNAL,'external_bytes':total_bytes,'records':records,
                 'failed_stage':stage,'exception_type':type(exc).__name__,
                 'source_text_or_error_payload_logged':False,'author_release_allowed':False}
        write_new(output.with_suffix('.failure.json'),failure)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('draft','freeze','build'))
    parser.add_argument('--protocol',required=True)
    args=parser.parse_args()
    if args.mode == 'build':
        result=build(args.protocol)
        print(json.dumps({k:result[k] for k in ('status','materialized_bundles','retained_occurrence_entries','external_bytes')}))
    else:
        write_new(args.protocol,protocol_value(draft=args.mode == 'draft'))
        print(json.dumps({'status':args.mode,'path':args.protocol,'sha256':digest(args.protocol)}))

if __name__ == '__main__':main()

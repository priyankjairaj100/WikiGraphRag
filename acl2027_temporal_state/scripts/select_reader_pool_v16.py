#!/usr/bin/env python3
"""Frozen source-purpose table pool; no questions, references, or model outputs."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('pool_table_views_v16', ROOT / 'scripts/build_table_views_v16.py')
views = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(views)
LIMIT = 25_000_000
RESERVE = 65536
RULE = {
    'leaf_tables_only': True, 'layout_parent_tables_permitted': True,
    'complete_row_cell_ownership': True, 'recognized_hiding_cues_allowed': False,
    'numeric_occurrence_min': 4, 'numeric_occurrence_max': 32,
    'minimum_resolved_normalized_duration_facts': 4,
    'minimum_distinct_resolved_duration_periods': 2,
    'period_identity': 'exact_reported_period_object_among_binding_resolved_duration_facts_including_nil_or_unsupported',
    'row_text_regex': r'\b(?:revenues?|net\s+sales)\b', 'regex_flags': 'IGNORECASE_UNICODE',
    'complete_dom_text_max_characters': 12000, 'selected_tables_per_source': 2,
    'selection_order': 'table_ordinal_xml_document_order',
    'raw_dom_text_definition': 'frozen_table_view_own_text_with_xml_whitespace_collapse_nbsp_retained',
    'complete_evidence_claim': False,
}
REASONS = ('has_child_table', 'incomplete_row_cell_ownership', 'recognized_hiding_cue',
           'numeric_occurrence_count_outside_4_32', 'fewer_than_4_usable_duration_facts',
           'fewer_than_2_resolved_duration_periods', 'missing_revenue_row_label',
           'complete_dom_text_exceeds_12000_characters')

class PoolError(ValueError):
    pass

sha = views.sha
digest = views.digest
encoded = views.encoded
utc = views.utc


def new_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write('\n')


def confined(base, name):
    base = Path(base).resolve()
    if not name or Path(name).is_absolute() or Path(name).name != name:
        raise PoolError('artifact_basename_required')
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise PoolError('artifact_path_escapes_root')
    return path


def load_view_records(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def period_key(fact):
    if fact.get('binding_status') != 'reported_aspects_resolved':
        return None
    period = fact.get('reported_aspects', {}).get('period')
    if not isinstance(period, dict) or period.get('kind') != 'duration':
        return None
    return json.dumps(period, sort_keys=True, separators=(',', ':'))


def joined_facts(table, typed):
    result = []
    seen = set()
    for link in table['fact_links']:
        ordinal = link['fact_ordinal']
        if ordinal in seen or ordinal not in typed:
            raise PoolError('fact_ordinal_missing_or_duplicate')
        seen.add(ordinal)
        fact = typed[ordinal]
        anchor = fact.get('anchor', {})
        if any(anchor.get(k) != v for k, v in link['anchor'].items()):
            raise PoolError('table_typed_fact_anchor_mismatch')
        if anchor.get('dom_path') != table['dom_path'] + link['path_from_table']:
            raise PoolError('table_typed_fact_locator_mismatch')
        result.append(fact)
    return result


def assess_table(table, facts, full_text, *, descendant_hiding=False):
    """All predicates, not sequential culling; all nil/unsupported facts retained."""
    periods = {key for fact in facts if (key := period_key(fact)) is not None}
    usable = sum(fact.get('numeric_status') == 'normalized' and period_key(fact) is not None
                 for fact in facts)
    hiding = descendant_hiding or table['visibility']['status'] != 'no_recognized_hiding_cue'
    hiding = hiding or any(cell['visibility']['status'] != 'no_recognized_hiding_cue'
                          for row in table['rows'] for cell in row['cells'])
    row_match = any(re.search(RULE['row_text_regex'], row['text'], re.I) is not None for row in table['rows'])
    conditions = [
        bool(table['child_table_ordinals']),
        bool(table['orphan_cells']) or any(link['row_cell_index'] is None for link in table['fact_links']),
        bool(hiding),
        not (4 <= len(facts) <= 32),
        usable < 4,
        len(periods) < 2,
        not row_match,
        len(full_text) > 12000,
    ]
    reasons = [reason for reason, fail in zip(REASONS, conditions) if fail]
    return {'eligible': not reasons, 'rejection_reasons': reasons,
            'fact_count': len(facts), 'usable_duration_count': usable,
            'resolved_duration_period_count': len(periods), 'dom_text_characters': len(full_text),
            'numeric_status_counts': dict(Counter(f.get('numeric_status', 'missing') for f in facts)),
            'binding_status_counts': dict(Counter(f.get('binding_status', 'missing') for f in facts))}


def choose_tables(assessments):
    ordinals = [a['table_ordinal'] for a in assessments]
    if ordinals != sorted(set(ordinals)):
        raise PoolError('table_ordinals_not_unique_document_order')
    eligible = [a for a in assessments if a['eligible']]
    return eligible[:2]


def author_packet(table, full_text, paragraphs, source_card):
    """No typed facts, concept names/context IDs, gold mappings or normalization."""
    return {'record_type': 'author_table', 'source_card': deepcopy(source_card),
            'table_ordinal': table['table_ordinal'], 'parent_table_ordinal': table['parent_table_ordinal'],
            'dom_path': table['dom_path'], 'anchor': deepcopy(table['anchor']),
            'rows': deepcopy(table['rows']), 'captions': deepcopy(table['captions']),
            'whole_dom_table_text': full_text, 'paragraph_refs': deepcopy(table['paragraph_refs']),
            'neighbor_paragraphs': deepcopy(paragraphs),
            'rendering_verified': False, 'computed_css_evaluated': False,
            'complete_evidence_verified': False, 'question_or_reference': None}


def source_card(identity):
    allowed = {'EntityRegistrantName', 'DocumentType', 'DocumentPeriodEndDate',
               'DocumentFiscalYearFocus', 'DocumentFiscalPeriodFocus', 'EntityCentralIndexKey'}
    fields = []
    for field in identity['identity_facts']:
        if field['name'].split(':')[-1] in allowed:
            fields.append({'field': field['name'].split(':')[-1], 'text': field['value']})
    return {'source_path': identity['source_path'], 'source_sha256': identity['source_sha256'],
            'source_identity_fields': fields,
            'internal_fiscal_year_values': identity['document_fiscal_year_values'],
            'filename_matches_internal_fiscal_year': identity['filename_matches_document_fiscal_year'],
            'year_source': 'frozen_source_identity_structure_document_fiscal_year_values',
            'filename_year_inference': False, 'independent_financial_verification': False}


class Writer:
    def __init__(self, limit=LIMIT-RESERVE):
        self.limit, self.bytes = limit, 0
    def write(self, stream, record):
        payload = encoded(record)
        if self.bytes + len(payload) > self.limit:
            raise PoolError('external_output_budget_exceeded')
        stream.write(payload)
        self.bytes += len(payload)


def prepare_protocol(path, *, inventory_path, view_protocol_path):
    if Path(path).exists():
        raise PoolError('refusing_to_overwrite_protocol')
    inventory = json.loads(Path(inventory_path).read_text())
    view_protocol = json.loads(Path(view_protocol_path).read_text())
    if inventory['status'] != 'completed' or len(inventory['records']) != 12:
        raise PoolError('all_12_completed_amended_views_required')
    if not str(inventory_path).endswith('table_view_inventory_v16_1.json'):
        raise PoolError('amended_complete_inventory_required')
    if any(r['status'] != 'completed' for r in inventory['records']):
        raise PoolError('all_12_completed_amended_views_required')
    by_source = {r['source_path']: r for r in inventory['records']}
    sources = []
    for item in view_protocol['sources']:
        row = by_source[item['source_path']]
        external = row['external_records']
        if not external['filename'].endswith('.jsonl.gz') or not external.get('complete'):
            raise PoolError('complete_gzip_view_required')
        sources.append(dict(item, views_filename=external['filename'], views_bytes=external['bytes'],
                            views_sha256=external['sha256']))
    if len(sources) != 12 or len({x['source_path'] for x in sources}) != 12:
        raise PoolError('source_population_mismatch')
    inputs = ['data/fresh_source_audit_v14/source_identity_structure.json',
              'data/typed_reader_v15/execution_protocol_v15_1.json',
              'results/typed_reader_execution_v15_1.json',
              'data/reader_binding_v16/table_view_protocol.json',
              'results/table_view_inventory_v16.json',
              'results/table_storage_independent_audit_v16.json',
              'results/reader_pool_code_review_v16.json',
              str(Path(view_protocol_path).resolve().relative_to(ROOT)),
              str(Path(inventory_path).resolve().relative_to(ROOT)),
              'docs/reader_pool_contract_v16.txt']
    code = ['scripts/select_reader_pool_v16.py', 'tests/test_reader_pool_v16.py',
            'tests/test_reader_pool_adversarial_v16.py',
            'scripts/build_table_views_v16.py', 'src/temporal_state/typed_reader_v15_1.py',
            'src/temporal_state/__init__.py']
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'), pattern='test_reader_pool*v16.py')
    transcript = io.StringIO()
    checked = unittest.TextTestRunner(stream=transcript, verbosity=1).run(suite)
    if not checked.wasSuccessful() or checked.skipped or checked.testsRun < 42:
        raise PoolError('authored_control_suite_failed_or_incomplete')
    controls = {'command': 'PYTHONPATH=src python3 -m unittest discover -s tests -p test_reader_pool*v16.py -q',
                'tests_run': checked.testsRun, 'skipped': len(checked.skipped),
                'failures': len(checked.failures), 'errors': len(checked.errors),
                'transcript_sha256': sha(transcript.getvalue().encode()),
                'scope': 'Authored fixtures only; rerun inside protocol preparation before first selection.'}
    review = json.loads((ROOT/'results/reader_pool_code_review_v16.json').read_text())
    if review.get('open_blocking_findings') != 0 or review.get('status') != 'passed_bounded_code_review_before_selection':
        raise PoolError('independent_code_review_not_passed')
    if any(digest(ROOT/name) != expected for name, expected in review['input_bindings'].items()):
        raise PoolError('independent_code_review_bindings_stale')
    protocol = {'schema_version': 'reader_question_pool_protocol_v16', 'frozen_at_utc': utc(),
                'rule': RULE, 'source_denominator': 12, 'planned_table_slots': 24,
                'external_output_limit_bytes': LIMIT, 'private_metadata_reserve_bytes': RESERVE,
                'sources': sources, 'code_bindings': {p: digest(ROOT/p) for p in code},
                'input_bindings': {p: digest(ROOT/p) for p in inputs},
                'runtime_bindings': views.runtime_bindings(),
                'inventory_path': str(Path(inventory_path).resolve().relative_to(ROOT)),
                'view_protocol_path': str(Path(view_protocol_path).resolve().relative_to(ROOT)),
                'declaration_timing': 'Code/rules/authored controls frozen before first pool selection; source values and QA references not used for selection design.',
                'scope': 'Purpose-selected revenue-labeled reported-financial development tables; not random, held-out or verified complete QA evidence.',
                'question_authoring_performed': False, 'natural_QA_predictions': 0, 'model_calls': 0,
                'pre_freeze_authored_tests': controls,
                'pre_freeze_corrections': ['Independent authored review caught cross-row net/sales regex match; fixed to test each row separately and added regression before source selection.']}
    new_json(path, protocol)
    return protocol


def preflight(protocol, source_dir, typed_dir, view_dir):
    errors = []
    if protocol.get('schema_version') != 'reader_question_pool_protocol_v16' or protocol.get('rule') != RULE:
        errors.append('protocol_or_rule_mismatch')
    if (protocol.get('inventory_path') != 'results/table_view_inventory_v16_1.json' or
            protocol.get('view_protocol_path') != 'data/reader_binding_v16/table_view_protocol_v16_1.json'):
        errors.append('parent_artifact_path_mismatch')
    if protocol.get('external_output_limit_bytes') != LIMIT or protocol.get('private_metadata_reserve_bytes') != RESERVE:
        errors.append('external_budget_mismatch')
    if len(protocol.get('sources', [])) != 12 or len({r['source_path'] for r in protocol.get('sources', [])}) != 12:
        errors.append('source_denominator_mismatch')
    for group in ('code_bindings', 'input_bindings'):
        for name, value in protocol.get(group, {}).items():
            p = (ROOT/name).resolve()
            if not p.is_relative_to(ROOT) or not p.is_file() or digest(p) != value:
                errors.append(group + '_mismatch:' + name)
    required = {'scripts/select_reader_pool_v16.py', 'tests/test_reader_pool_v16.py', 'tests/test_reader_pool_adversarial_v16.py', 'scripts/build_table_views_v16.py',
                'src/temporal_state/typed_reader_v15_1.py', 'src/temporal_state/__init__.py'}
    if not required.issubset(protocol.get('code_bindings', {})):
        errors.append('required_code_bindings_missing')
    required_inputs = {'data/fresh_source_audit_v14/source_identity_structure.json',
                       'data/typed_reader_v15/execution_protocol_v15_1.json',
                       'results/typed_reader_execution_v15_1.json',
                       'data/reader_binding_v16/table_view_protocol.json',
                       'results/table_view_inventory_v16.json',
              'results/table_storage_independent_audit_v16.json',
              'results/reader_pool_code_review_v16.json', 'results/table_view_inventory_v16_1.json',
                       'data/reader_binding_v16/table_view_protocol_v16_1.json',
                       'docs/reader_pool_contract_v16.txt'}
    if not required_inputs.issubset(protocol.get('input_bindings', {})):
        errors.append('required_input_bindings_missing')
    if views.runtime_bindings() != protocol.get('runtime_bindings'):
        errors.append('runtime_binding_mismatch')
    for item in protocol.get('sources', []):
        for base, name_key, size_key, hash_key in (
            (source_dir, 'external_filename', 'expected_bytes', 'expected_sha256'),
            (typed_dir, 'typed_filename', 'typed_bytes', 'typed_sha256'),
            (view_dir, 'views_filename', 'views_bytes', 'views_sha256')):
            try:
                p = confined(base, item[name_key])
                if not p.is_file() or p.stat().st_size != item[size_key] or digest(p) != item[hash_key]:
                    errors.append('external_artifact_mismatch:' + item['source_path'] + ':' + name_key)
            except PoolError:
                errors.append('external_artifact_path_invalid:' + item['source_path'])
    if not errors:
        inventory = json.loads((ROOT/protocol['inventory_path']).read_text())
        parent = json.loads((ROOT/protocol['view_protocol_path']).read_text())
        by_path = {r['source_path']: r for r in inventory['records']}
        expected = []
        for item in parent['sources']:
            e = by_path[item['source_path']]['external_records']
            expected.append(dict(item, views_filename=e['filename'], views_bytes=e['bytes'], views_sha256=e['sha256']))
        if protocol['sources'] != expected or inventory['status'] != 'completed':
            errors.append('source_parent_lineage_mismatch')
    return errors


def execute(protocol_path, *, source_dir, typed_dir, view_dir, external_dir, output):
    if output.exists() or output.is_symlink() or external_dir.exists() or external_dir.is_symlink():
        raise PoolError('refusing_to_overwrite_existing_attempt')
    if external_dir.resolve().is_relative_to(ROOT.parent.resolve()):
        raise PoolError('private_output_must_be_outside_repository')
    if output.resolve().is_relative_to(external_dir.resolve()):
        raise PoolError('public_output_must_be_outside_private_attempt')
    protocol = json.loads(protocol_path.read_text())
    errors = preflight(protocol, source_dir, typed_dir, view_dir)
    result = {'schema_version': 'reader_question_pool_result_v16', 'started_at_utc': utc(),
              'protocol_sha256': digest(protocol_path), 'status': 'preflight_failed' if errors else 'running',
              'preflight_errors': errors, 'source_denominator': 12, 'planned_table_slots': 24,
              'question_authoring_performed': False, 'natural_QA_predictions': 0, 'model_calls': 0,
              'rendering_verified': False, 'complete_evidence_verified': False,
              'source_selection': 'purpose_selected_development_domain', 'records': []}
    if not errors:
        identities = json.loads((ROOT/'data/fresh_source_audit_v14/source_identity_structure.json').read_text())
        identities = {row['source_path']: row for row in identities['records']}
        external_dir.mkdir(parents=True)
        new_json(external_dir/'attempt_started.json', {'started_at_utc': result['started_at_utc'], 'protocol_sha256': result['protocol_sha256']})
        writer = Writer()
        total_counts, all_rejections = Counter(), Counter()
        private_failures = []
        for item in protocol['sources']:
            entry = {'source_path': item['source_path'], 'source_sha256': item['expected_sha256'],
                     'status': 'running', 'planned_table_slots': 2}
            created = []
            try:
                source = confined(source_dir, item['external_filename']).read_bytes()
                records = load_view_records(confined(view_dir, item['views_filename']))
                meta = records[0]
                if meta.get('record_type') != 'source' or meta['source_path'] != item['source_path'] or meta['source_sha256'] != item['expected_sha256']:
                    raise PoolError('view_source_identity_mismatch')
                typed_list = views.load_typed(confined(typed_dir, item['typed_filename']))
                typed = {f['fact_ordinal']: f for f in typed_list}
                if len(typed) != len(typed_list) or len(typed) != item['expected_nonfraction_count']:
                    raise PoolError('typed_fact_population_mismatch')
                if any(f['source_sha256'] != item['expected_sha256'] for f in typed_list):
                    raise PoolError('typed_source_hash_mismatch')
                root, paths, spans = views.parse_bound(source)
                nodes = {path: node for node, path in paths.items()}
                tables = [r for r in records if r['record_type'] == 'table']
                paragraphs = {r['paragraph_ordinal']: r for r in records if r['record_type'] == 'paragraph'}
                source_tables = [n for n in paths if n.tag == views.TABLE]
                if (len(tables) != meta['table_count'] or len(tables) != len(source_tables)
                    or [r['table_ordinal'] for r in tables] != list(range(len(source_tables)))
                    or [r['dom_path'] for r in tables] != [paths[n] for n in source_tables]):
                    raise PoolError('view_table_population_or_order_mismatch')
                assessments, details = [], {}
                for table in tables:
                    node = nodes[table['dom_path']]
                    if node.tag != views.TABLE or views.anchor(node, paths, spans, source) != table['anchor']:
                        raise PoolError('table_anchor_mismatch')
                    facts = joined_facts(table, typed)
                    full_text = views.own_text(node)
                    hiding = any(views.visibility(n, paths)['status'] != 'no_recognized_hiding_cue'
                                 for n in node.iter() if isinstance(n.tag, str))
                    assessment = assess_table(table, facts, full_text, descendant_hiding=hiding)
                    assessment['table_ordinal'] = table['table_ordinal']
                    assessments.append(assessment)
                    details[table['table_ordinal']] = (table, facts, full_text, node)
                selected = choose_tables(assessments)
                rejection_counts = Counter(reason for a in assessments for reason in a['rejection_reasons'])
                first_rejections = Counter(a['rejection_reasons'][0] for a in assessments if a['rejection_reasons'])
                identity = identities[item['source_path']]
                if identity['source_sha256'] != item['expected_sha256']:
                    raise PoolError('frozen_identity_hash_mismatch')
                card = source_card(identity)
                stem = Path(item['external_filename']).stem
                author_path = external_dir/(stem+'.author.jsonl')
                review_path = external_dir/(stem+'.review.jsonl')
                created = [author_path, review_path]
                with author_path.open('xb') as author, review_path.open('xb') as review:
                    for selected_item in selected:
                        table, facts, full_text, node = details[selected_item['table_ordinal']]
                        refs = table['paragraph_refs']['preceding'] + table['paragraph_refs']['following']
                        neighbors = [paragraphs[i] for i in refs]
                        writer.write(author, author_packet(table, full_text, neighbors, card))
                        ancestors = []
                        for ancestor in views.ancestors_including(node.getparent()):
                            n = spans[paths[ancestor]]
                            opening = source[n.start:n.opening_stop]
                            ancestors.append({'dom_path': paths[ancestor], 'tag': ancestor.tag,
                                              'anchor': views.anchor(ancestor, paths, spans, source),
                                              'opening_byte_start': n.start, 'opening_byte_stop': n.opening_stop,
                                              'opening_sha256': sha(opening), 'opening_tag_utf8': opening.decode('utf-8')})
                        writer.write(review, {'record_type': 'review_table_fact_join', 'source_sha256': item['expected_sha256'],
                                              'table': table, 'facts': facts, 'source_card': card,
                                              'ancestor_wrappers': ancestors, 'selection_assessment': selected_item,
                                              'author_packet_sha256': sha(encoded(author_packet(table, full_text, neighbors, card))),
                                              'rendering_verified': False, 'semantic_attachment_verified': False})
                entry.update(status='completed', table_denominator=len(tables),
                             eligible_tables=sum(a['eligible'] for a in assessments), selected_tables=len(selected),
                             unfilled_table_slots=2-len(selected),
                             eligible_not_selected=max(0, sum(a['eligible'] for a in assessments)-2),
                             rejection_counts=dict(rejection_counts), first_rejection_counts=dict(first_rejections),
                             selected_table_summaries=selected)
                total_counts.update({k: entry[k] for k in ('table_denominator','eligible_tables','selected_tables','unfilled_table_slots','eligible_not_selected')})
                all_rejections.update(rejection_counts)
                del source, records, typed_list, typed, root, paths, spans, nodes, details, tables, paragraphs
            except Exception as exc:
                entry.update(status='failed', failure_code=str(exc) if isinstance(exc, PoolError) else 'technical_exception', error_class=type(exc).__name__)
                private_failures.append({'source_path': item['source_path'], 'error_class': type(exc).__name__, 'message': str(exc)[:2048]})
            finally:
                entry['external_artifacts'] = [{'filename': p.name, 'bytes': p.stat().st_size, 'sha256': digest(p), 'complete': entry['status']=='completed'} for p in created if p.exists()]
                result['records'].append(entry)
                gc.collect()
        result['totals'] = dict(total_counts)
        result['totals']['completed_sources'] = sum(r['status']=='completed' for r in result['records'])
        result['totals']['failed_source_table_slots'] = 2 * (12-result['totals']['completed_sources'])
        result['totals']['unfilled_table_slots_including_failed_sources'] = 24-total_counts['selected_tables']
        result['rejection_counts'] = dict(all_rejections)
        result['status'] = 'completed' if all(r['status']=='completed' for r in result['records']) else 'failed_sources_retained'
        result['external_payload_bytes'] = writer.bytes
        new_json(external_dir/'attempt_finished.json', {'finished_at_utc': utc(), 'status': result['status'], 'private_failures': private_failures})
        result['external_total_bytes'] = sum(p.stat().st_size for p in external_dir.iterdir() if p.is_file())
        result['external_output_limit_bytes'] = LIMIT
        if result['external_total_bytes'] > LIMIT:
            result['status'] = 'external_budget_violation'
    result['finished_at_utc'] = utc()
    new_json(output, result)
    return result


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='command', required=True)
    f=sub.add_parser('freeze'); f.add_argument('--protocol',type=Path,required=True)
    f.add_argument('--table-inventory',type=Path,required=True); f.add_argument('--table-protocol',type=Path,required=True)
    r=sub.add_parser('run'); r.add_argument('--protocol',type=Path,required=True)
    for name in ('sources','typed-records','table-views','external-output','output'):
        r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.command=='freeze':
        result=prepare_protocol(a.protocol,inventory_path=a.table_inventory,view_protocol_path=a.table_protocol)
        print(json.dumps({'status':'frozen','protocol_sha256':digest(a.protocol),'source_denominator':12}))
    else:
        result=execute(a.protocol,source_dir=a.sources,typed_dir=a.typed_records,view_dir=a.table_views,external_dir=a.external_output,output=a.output)
        print(json.dumps({k:result[k] for k in ('status','source_denominator','totals') if k in result},sort_keys=True))
        raise SystemExit(0 if result['status']=='completed' else 1)

if __name__=='__main__':
    main()

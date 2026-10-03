#!/usr/bin/env python3
"""Validate the frozen source-only ambiguity audit without editing its base graph.

Checks structure, source integrity and representation contracts, not entailment,
mutual exclusion or completeness. Proposals and defects require adjudication.
"""
import argparse
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    'candidate_graph_validation_v06', ROOT / 'scripts/validate_candidate_graph_v06.py')
_GRAPH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GRAPH)
OLD = _GRAPH.old

CATEGORIES = {
    'mutually_exclusive_reading', 'semantic_equivalence',
    'weaker_or_stronger_information', 'separate_assertion',
    'cross_source_disagreement', 'unsupported_alternative', 'representation_defect',
}
SEMANTIC_FIELDS = {
    'subject', 'relation', 'value', 'scope', 'role_qualifier', 'polarity',
    'modality', 'reported_at', 'start', 'start.lower', 'start.upper',
    'end', 'end.lower', 'end.upper', 'state_observed_at',
}
DEFAULT_PROTOCOL = ROOT / 'data/natural_model_pilot_v07/candidate_expansion_protocol.json'
DEFAULT_RECEIPT = ROOT / 'data/natural_model_pilot_v07/candidate_audit_receipt.json'


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def object_keys(value, keys, where):
    check(type(value) is dict, where + ':expected_object')
    check(set(value) == set(keys), where + ':wrong_keys')


def text(value, where):
    check(type(value) is str and bool(value.strip()), where + ':expected_nonempty_string')


def sequence(value, where, nonempty=False):
    check(type(value) is list, where + ':expected_array')
    check(not nonempty or bool(value), where + ':empty_array')


def string_list(value, where, nonempty=False):
    sequence(value, where, nonempty)
    for item in value:
        text(item, where)
    check(len(value) == len(set(value)), where + ':duplicate_item')


def validate_audit(raw, request, base_graph, graph_schema, protocol):
    """Validate supplied objects; the inherited graph gate reads its versioned schema."""
    audit = OLD.strict_json(raw)
    object_keys(audit, ('schema_version', 'mention_audits', 'proposed_links',
                      'representation_defects', 'cross_source_findings',
                      'overflow', 'overflow_descriptions'), 'audit')
    check(audit['schema_version'] == 'candidate_ambiguity_audit_v0.7', 'audit:schema_version')
    check(type(audit['overflow']) is bool, 'audit:overflow_not_boolean')
    string_list(audit['overflow_descriptions'], 'overflow_descriptions')
    check(bool(audit['overflow_descriptions']) == audit['overflow'], 'overflow_description_mismatch')
    check(protocol['schema_version'] == 'candidate_expansion_protocol_v0.7', 'protocol:schema_version')
    limits = protocol['limits']
    check(limits['new_mentions_allowed'] == 0, 'unsupported_protocol_new_mentions')
    check(limits['max_resolved_per_mention'] == 4, 'unsupported_protocol_reading_cap')
    base_raw = json.dumps(base_graph, ensure_ascii=False)
    base_report = _GRAPH.validate_graph(base_raw, request)
    check(len(base_graph['mentions']) == limits['existing_mentions'], 'protocol_mention_count')
    check(len(base_graph['mentions']) <= limits['max_mentions'], 'mention_cap')
    sources = {source['source_id']: source for source in request['sources']}
    mentions = {mention['mention_id']: mention for mention in base_graph['mentions']}
    readings = {reading['candidate_id']: reading
                for mention in base_graph['mentions'] for reading in mention['readings']}
    mention_of = {reading['candidate_id']: mention['mention_id']
                  for mention in base_graph['mentions'] for reading in mention['readings']}
    aligned = []

    def evidence(rows, where):
        sequence(rows, where, nonempty=True)
        for row in rows:
            object_keys(row, ('source_id', 'quote'), where)
            text(row['source_id'], where + '.source_id')
            text(row['quote'], where + '.quote')
        spans = OLD.align_evidence(rows, sources)
        aligned.append({'location': where, 'spans': spans})
        return spans

    sequence(audit['mention_audits'], 'mention_audits', nonempty=True)
    check(len(audit['mention_audits']) == len(mentions), 'mention_coverage_count')
    for row in audit['mention_audits']:
        object_keys(row, ('mention_id', 'disposition', 'summary',
                         'considered_differences', 'proposals'), 'mention_audit')
    check([row['mention_id'] for row in audit['mention_audits']] == list(mentions),
          'mention_coverage_order_or_duplicate')
    proposed, proposed_ids, defect_mentions = [], [], []
    augmented = deepcopy(base_graph)  # Validation-only object; never emitted as a graph.
    for index, row in enumerate(audit['mention_audits']):
        mid = row['mention_id']
        text(row['summary'], mid + '.summary')
        check(row['disposition'] in {'no_supported_alternative', 'proposes_alternative',
                                    'representation_defect'}, mid + ':invalid_disposition')
        sequence(row['considered_differences'], mid + '.considered_differences')
        classifications = []
        for n, difference in enumerate(row['considered_differences']):
            where = f'{mid}.considered_differences[{n}]'
            object_keys(difference, ('classification', 'description', 'reason', 'evidence'), where)
            check(difference['classification'] in CATEGORIES, where + ':invalid_classification')
            text(difference['description'], where + '.description')
            text(difference['reason'], where + '.reason')
            evidence(difference['evidence'], where + '.evidence')
            classifications.append(difference['classification'])
        sequence(row['proposals'], mid + '.proposals')
        check(bool(row['proposals']) == (row['disposition'] == 'proposes_alternative'),
              mid + ':proposal_disposition_mismatch')
        check(not row['proposals'] or 'mutually_exclusive_reading' in classifications,
              mid + ':proposal_without_mutually_exclusive_classification')
        if row['disposition'] == 'representation_defect':
            defect_mentions.append(mid)
        check(len(mentions[mid]['readings']) + len(row['proposals']) <= limits['max_resolved_per_mention'],
              mid + ':reading_cap_no_pruning')
        for n, proposal in enumerate(row['proposals']):
            where = f'{mid}.proposals[{n}]'
            object_keys(proposal, ('competes_with_candidate_id', 'changed_fields',
                                  'support_summary', 'incompatibility_summary', 'reading'), where)
            check(proposal['competes_with_candidate_id'] in {
                reading['candidate_id'] for reading in mentions[mid]['readings']},
                where + ':competitor_not_existing_same_mention')
            string_list(proposal['changed_fields'], where + '.changed_fields', nonempty=True)
            check(set(proposal['changed_fields']) <= SEMANTIC_FIELDS, where + ':invalid_semantic_field')
            text(proposal['support_summary'], where + '.support_summary')
            text(proposal['incompatibility_summary'], where + '.incompatibility_summary')
            reading = proposal['reading']
            OLD.validate_schema(reading, graph_schema['$defs']['reading'], root=graph_schema)
            expected = f'p07_{len(proposed) + 1:03d}'
            check(reading['candidate_id'] == expected, where + ':temporary_candidate_id_order')
            check(expected not in readings, where + ':candidate_id_collision')
            evidence(reading['evidence'], where + '.reading.evidence')
            proposed.append(proposal)
            proposed_ids.append(expected)
            readings[expected] = reading
            mention_of[expected] = mid
            augmented['mentions'][index]['readings'].append(reading)
    check(sum(len(m['readings']) for m in augmented['mentions']) <= limits['max_total_resolved'],
          'total_reading_cap_no_pruning')
    sequence(audit['proposed_links'], 'proposed_links')
    base_link_ids = {link['link_id'] for link in base_graph['links']}
    for index, link in enumerate(audit['proposed_links']):
        where = f'proposed_links[{index}]'
        OLD.validate_schema(link, graph_schema['$defs']['link'], root=graph_schema)
        check(link['link_id'] == f'pl07_{index + 1:03d}', where + ':temporary_link_id_order')
        check(link['link_id'] not in base_link_ids, where + ':link_id_collision')
        check(link['candidate_id'] in readings and link['target_candidate_id'] in readings,
              where + ':unknown_link_endpoint')
        check(link['candidate_id'] in proposed_ids or link['target_candidate_id'] in proposed_ids,
              where + ':link_does_not_involve_proposed_reading')
        left, right = readings[link['candidate_id']], readings[link['target_candidate_id']]
        if link['relation'] in {'RESTATES', 'CHANGES'}:
            check(left['modality'] == right['modality'], where + ':cross_modality_link')
            check(all(left[key] == right[key] for key in ('subject', 'relation', 'scope', 'role_qualifier')),
                  where + ':link_key_mismatch')
            if link['relation'] == 'RESTATES':
                check(left['value'] == right['value'] and left['polarity'] == right['polarity'],
                      where + ':restates_value_or_polarity_mismatch')
            else:
                check(left['polarity'] == right['polarity'] == 'positive' and left['value'] != right['value'],
                      where + ':changes_requires_distinct_positive_values')
        evidence(link['evidence'], where + '.evidence')
        augmented['links'].append(link)
    # Reuse exact span, anchor overlap, date, correction-certificate and reference gates.
    proposal_graph_report = _GRAPH.validate_graph(json.dumps(augmented, ensure_ascii=False), request)
    sequence(audit['representation_defects'], 'representation_defects')
    known_ids = set(mentions) | set(readings) | base_link_ids | {
        link['link_id'] for link in audit['proposed_links']} | {
        row['alias_id'] for row in base_graph['aliases']} | {
        row['authority_id'] for row in base_graph['authorities']} | set(sources)
    for index, row in enumerate(audit['representation_defects']):
        where = f'representation_defects[{index}]'
        object_keys(row, ('affected_ids', 'description', 'evidence'), where)
        string_list(row['affected_ids'], where + '.affected_ids', nonempty=True)
        check(set(row['affected_ids']) <= known_ids, where + ':unknown_affected_id')
        text(row['description'], where + '.description')
        evidence(row['evidence'], where + '.evidence')
    sequence(audit['cross_source_findings'], 'cross_source_findings')
    for index, row in enumerate(audit['cross_source_findings']):
        where = f'cross_source_findings[{index}]'
        object_keys(row, ('source_ids', 'description', 'evidence'), where)
        string_list(row['source_ids'], where + '.source_ids', nonempty=True)
        check(set(row['source_ids']) <= set(sources), where + ':unknown_source')
        text(row['description'], where + '.description')
        spans = evidence(row['evidence'], where + '.evidence')
        check({span['source_id'] for span in spans} == set(row['source_ids']),
              where + ':declared_source_evidence_mismatch')
    pending = bool(proposed or audit['representation_defects'] or defect_mentions)
    blocked = bool(audit['overflow'])
    status = ('blocked_overflow' if blocked else 'pending_source_only_adjudication'
              if pending else 'valid_zero_addition_report')
    return {
        'schema_version': 'candidate_ambiguity_validation_v0.7',
        'validation_status': status,
        'raw_output_sha256': sha256(raw.encode()).hexdigest(),
        'mentions_audited': len(audit['mention_audits']),
        'proposed_readings': len(proposed),
        'proposed_links': len(audit['proposed_links']),
        'representation_defects': len(audit['representation_defects']),
        'mentions_flagged_as_defective': defect_mentions,
        'cross_source_findings': len(audit['cross_source_findings']),
        'overflow': audit['overflow'],
        'pending_source_only_adjudication': pending,
        'graph_mutated': False,
        'validator_performed_repair': False,
        'base_resolved_readings': base_report['resolved_readings'],
        'proposed_graph_resolved_readings_for_representation_validation_only': proposal_graph_report['resolved_readings'],
        'aligned_audit_evidence': aligned,
        'validation_scope': 'Strict report shape, source and input integrity, coverage, IDs, caps, exact unique quotes, and proposed representation compatibility. Does not establish entailment, mutual exclusion, semantic correctness, completeness, adjudication or final cache validity.',
        'evidence_type': 'unversioned_source_only_model_audit',
    }


def negative_controls(raw, request, base_graph, graph_schema, protocol):
    audit = OLD.strict_json(raw)
    probes = []
    missing = deepcopy(audit)
    missing['mention_audits'].pop()
    probes.append(('missing_mention', missing))
    altered = deepcopy(audit)

    def alter_first_quote(node):
        if type(node) is dict:
            if set(node) == {'source_id', 'quote'}:
                node['quote'] += ' [ALTERED QUOTE NEGATIVE CONTROL]'
                return True
            return any(alter_first_quote(value) for value in node.values())
        if type(node) is list:
            return any(alter_first_quote(value) for value in node)
        return False

    if alter_first_quote(altered):
        probes.append(('altered_evidence_quote', altered))
    outcomes = []
    for name, probe in probes:
        try:
            validate_audit(json.dumps(probe, ensure_ascii=False), request, base_graph, graph_schema, protocol)
        except ValueError as exc:
            outcomes.append({'control': name, 'rejected': True, 'reason': str(exc)})
        else:
            raise AssertionError('negative_control_accepted:' + name)
    return outcomes



def validate_receipt(receipt, expected_inputs, raw_path, raw_bytes):
    """Bind declared lineage to caller-bound bytes; never open receipt paths."""
    object_keys(receipt, ('schema_version', 'recorded_at_utc', 'role', 'backend',
                         'agent_name', 'model_id', 'model_revision', 'fresh_context',
                         'allowed_inputs', 'prohibited_inputs', 'input_access_evidence',
                         'generation_calls', 'repair_calls', 'output', 'claim_scope'), 'receipt')
    check(receipt['schema_version'] == 'candidate_audit_receipt_v0.7', 'receipt:schema_version')
    check(receipt['role'] == 'source_only_ambiguity_auditor', 'receipt:wrong_role')
    check(receipt['backend'] == 'conversation_isolated_agent', 'receipt:wrong_backend')
    check(receipt['model_id'] == 'unversioned_conversational_model', 'receipt:wrong_model_id')
    check(receipt['model_revision'] is None, 'receipt:model_revision_must_be_null')
    check(receipt['fresh_context'] is True, 'receipt:fresh_context_declaration')
    check(type(receipt['generation_calls']) is int and receipt['generation_calls'] == 1,
          'receipt:generation_calls')
    check(type(receipt['repair_calls']) is int and receipt['repair_calls'] == 0,
          'receipt:repair_calls')
    for key in ('recorded_at_utc', 'agent_name', 'input_access_evidence', 'claim_scope'):
        text(receipt[key], 'receipt.' + key)
    string_list(receipt['prohibited_inputs'], 'receipt.prohibited_inputs', nonempty=True)
    check(set(receipt['prohibited_inputs']) == {
        'QA', 'gold', 'scores', 'decoder_results', 'external_sources', 'other_project_files'},
        'receipt:prohibited_input_declaration')
    sequence(receipt['allowed_inputs'], 'receipt.allowed_inputs', nonempty=True)
    check(len(expected_inputs) == 5, 'receipt:expected_five_caller_bound_inputs')
    actual_inputs = {}
    for row in receipt['allowed_inputs']:
        object_keys(row, ('path', 'bytes', 'sha256'), 'receipt.input')
        text(row['path'], 'receipt.input.path')
        check(row['path'] not in actual_inputs, 'receipt:duplicate_input_path')
        check(row['path'] in expected_inputs, 'receipt:unexpected_input_path')
        expected = expected_inputs[row['path']]
        check(type(row['bytes']) is int and row['bytes'] == expected['bytes'],
              'receipt:input_byte_count_mismatch')
        check(row['sha256'] == expected['sha256'], 'receipt:input_hash_mismatch')
        actual_inputs[row['path']] = row
    check(set(actual_inputs) == set(expected_inputs), 'receipt:missing_input_path')
    output = receipt['output']
    object_keys(output, ('path', 'bytes', 'sha256'), 'receipt.output')
    check(output['path'] == raw_path, 'receipt:output_path_mismatch')
    check(type(output['bytes']) is int and output['bytes'] == len(raw_bytes),
          'receipt:output_byte_count_mismatch')
    check(output['sha256'] == sha256(raw_bytes).hexdigest(), 'receipt:output_hash_mismatch')
    return {
        'status': 'bound_to_five_caller_selected_inputs_and_raw_output',
        'role': receipt['role'], 'backend': receipt['backend'],
        'model_id': receipt['model_id'], 'model_revision': None,
        'generation_calls': 1, 'repair_calls': 0,
        'declared_fresh_context': receipt['fresh_context'],
        'receipt_paths_dereferenced': False,
        'scope': 'Checks recorded identity and exact declared input/output bytes. Fresh context and restricted access are declarations, not independently proven isolation, model-family independence or absence of other knowledge.',
    }


def receipt_negative_controls(receipt, expected_inputs, raw_path, raw_bytes):
    changed_hash = deepcopy(receipt)
    changed_hash['allowed_inputs'][0]['sha256'] = '0' * 64
    extra_path = deepcopy(receipt)
    extra_path['allowed_inputs'].append({'path': 'undeclared_input.json', 'bytes': 0,
                                        'sha256': sha256(b'').hexdigest()})
    outcomes = []
    for name, probe in [('receipt_input_hash_mismatch', changed_hash),
                        ('receipt_extra_input_path', extra_path)]:
        try:
            validate_receipt(probe, expected_inputs, raw_path, raw_bytes)
        except ValueError as exc:
            outcomes.append({'control': name, 'rejected': True, 'reason': str(exc)})
        else:
            raise AssertionError('negative_control_accepted:' + name)
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-output', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument('--receipt', type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument('--report', type=Path, default=ROOT / 'results/candidate_ambiguity_v07.json')
    args = parser.parse_args()
    protocol_bytes = args.protocol.read_bytes()
    protocol = OLD.strict_json(protocol_bytes.decode())
    inputs = {}
    bound_inputs = {}
    for role, record in protocol['inputs'].items():
        path = ROOT / record['path']
        check(path.resolve().is_relative_to(ROOT.resolve()), 'protocol_input_outside_project')
        payload = path.read_bytes()
        digest = sha256(payload).hexdigest()
        check(digest == record['sha256'], 'frozen_input_hash_mismatch:' + role)
        inputs[role] = payload.decode()
        bound_inputs[role] = {'path': record['path'], 'bytes': len(payload), 'sha256': digest}
    request = OLD.strict_json(inputs['source_request'])
    graph = OLD.strict_json(inputs['base_graph'])
    schema = OLD.strict_json(inputs['graph_schema'])
    raw_bytes = args.raw_output.read_bytes()
    raw = raw_bytes.decode()
    report = validate_audit(raw, request, graph, schema, protocol)
    report['frozen_protocol_sha256'] = sha256(protocol_bytes).hexdigest()
    report['bound_inputs'] = bound_inputs
    report['negative_controls'] = negative_controls(raw, request, graph, schema, protocol)
    # Only caller-bound paths supply bytes. Receipt paths are compared, never opened.
    check(args.protocol.resolve().is_relative_to(ROOT.resolve()), 'protocol_outside_project')
    check(args.raw_output.resolve().is_relative_to(ROOT.resolve()), 'raw_output_outside_project')
    protocol_path = args.protocol.resolve().relative_to(ROOT.resolve()).as_posix()
    raw_path = args.raw_output.resolve().relative_to(ROOT.resolve()).as_posix()
    expected_inputs = {record['path']: {'bytes': record['bytes'], 'sha256': record['sha256']}
                       for record in bound_inputs.values()}
    expected_inputs[protocol_path] = {'bytes': len(protocol_bytes),
                                      'sha256': sha256(protocol_bytes).hexdigest()}
    receipt_bytes = args.receipt.read_bytes()
    receipt = OLD.strict_json(receipt_bytes.decode())
    report['generation_receipt_validation'] = validate_receipt(
        receipt, expected_inputs, raw_path, raw_bytes)
    report['generation_receipt_sha256'] = sha256(receipt_bytes).hexdigest()
    report['receipt_negative_controls'] = receipt_negative_controls(
        receipt, expected_inputs, raw_path, raw_bytes)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(OLD.canonical(report) + b'\n')
    print(json.dumps({key: report[key] for key in ('validation_status', 'mentions_audited',
                      'proposed_readings', 'proposed_links', 'pending_source_only_adjudication')}))


if __name__ == '__main__':
    main()

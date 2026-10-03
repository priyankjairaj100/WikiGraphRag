#!/usr/bin/env python3
"""Strict offline scoring of the frozen v0.11 source-passage diagnostic.

Reads only reviewed exported predictions and frozen references. No model,
retrieval, tokenization, reference repair, output repair, or approximate numbers.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PROTOCOL = PROJECT / 'data/correction_gate_v11/reader_protocol.json'
REFERENCES = PROJECT / 'data/correction_gate_v11/reader_references_raw.json'
QUESTIONS = PROJECT / 'data/correction_gate_v11/reader_questions.json'
FROZEN = PROJECT / 'data/native_reader_v11/attempt02/frozen_inputs.json'
EXPORT_REVIEW = PROJECT / 'data/native_reader_v11/export_review.json'
PREDICTIONS = PROJECT / 'results/native_reader_v11_outputs.json'
OUTPUT = PROJECT / 'results/reader_evaluation_v11.json'
QUESTION_IDS = ['Q1', 'Q2', 'Q3']
CONDITION_IDS = ['article_then_notice', 'notice_then_article']
EXPOSURES = {'marketing_value', 'payments', 'physicians_receiving_payments', 'uncertain'}
AGREEMENTS = {'agree', 'disagree', 'uncertain'}
EVIDENCE_IDS = {'P1', 'P2', 'P3', 'P4'}
PROJECTION_FIELDS = {
    'item_id', 'response_sha256', 'content_sha256', 'content_utf8_bytes',
    'generated_token_ids_sha256', 'returned_generated_token_count', 'tokens_evaluated',
    'tokens_predicted', 'truncated', 'stop', 'stop_type', 'stopping_word_sha256',
    'output_budget_reached', 'timings', 'generation_settings_sha256',
    'content_export_status', 'valid_json', 'json_top_level_type', 'content'}


class EvaluationInputError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvaluationInputError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def duplicate_free_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def invalid_constant(value):
    raise EvaluationInputError('nonfinite JSON constant: ' + value)


def strict_json(text, *, decimals=False):
    try:
        return json.loads(text, object_pairs_hook=duplicate_free_object,
                          parse_constant=invalid_constant,
                          parse_float=Decimal if decimals else float,
                          parse_int=Decimal if decimals else int)
    except json.JSONDecodeError as error:
        # Retain location and parser reason without quoting generated content.
        raise EvaluationInputError(f'invalid JSON at character {error.pos}: {error.msg}') from error


def read(path, *, decimals=False):
    return strict_json(Path(path).read_text(), decimals=decimals)


def parse_answers(content):
    value = strict_json(content, decimals=True)
    require(isinstance(value, dict) and set(value) == {'answers'},
            'response must contain exactly the answers field')
    answers = value['answers']
    require(isinstance(answers, list) and len(answers) == 3, 'exactly three answer objects required')
    errors = []
    for qid, answer in zip(QUESTION_IDS, answers):
        try:
            require(isinstance(answer, dict), f'{qid}: answer must be an object')
            expected = {'question_id', 'agreement', 'evidence_ids'} if qid == 'Q2' else {
                'question_id', 'exposure', 'value', 'evidence_ids'}
            require(set(answer) == expected, f'{qid}: exact field set violated')
            require(answer['question_id'] == qid, f'{qid}: question order/identity violated')
            if qid == 'Q2':
                require(type(answer['agreement']) is str and answer['agreement'] in AGREEMENTS,
                        f'{qid}: invalid agreement enum')
            else:
                require(type(answer['exposure']) is str and answer['exposure'] in EXPOSURES,
                        f'{qid}: invalid exposure enum')
                number = answer['value']
                if answer['exposure'] == 'uncertain':
                    require(number is None, f'{qid}: uncertain exposure requires null value')
                else:
                    require(type(number) is Decimal and number.is_finite(),
                            f'{qid}: finite JSON numeric value required; boolean/string/null forbidden')
            evidence = answer['evidence_ids']
            require(isinstance(evidence, list) and len(evidence) > 0,
                    f'{qid}: nonempty evidence list required')
            require(all(type(ident) is str and ident in EVIDENCE_IDS for ident in evidence),
                    f'{qid}: unknown or nonstring evidence ID')
            require(len(set(evidence)) == len(evidence), f'{qid}: duplicate evidence ID')
        except EvaluationInputError as error:
            errors.append(str(error))
    require(not errors, '; '.join(errors))
    return answers


def bindings(protocol_path=PROTOCOL, reference_path=REFERENCES, question_path=QUESTIONS):
    protocol = read(protocol_path)
    require(protocol['schema_version'] == 'current_document_reader_protocol_v0.11',
            'unexpected protocol schema')
    require(protocol['native_outputs_at_freeze'] == 0, 'protocol freeze was not pre-output')
    require(list(protocol['conditions']) == CONDITION_IDS, 'condition identities/order changed')
    require(protocol['reference_sha256'] == sha(reference_path), 'frozen reference hash mismatch')
    require(protocol['questions_sha256'] == sha(question_path), 'frozen question hash mismatch')
    questions = read(question_path)
    require([q['question_id'] for q in questions['questions']] == QUESTION_IDS, 'question order changed')
    contract = questions['response_contract']
    require(set(contract['exposure_enum']) == EXPOSURES and
            set(contract['agreement_enum']) == AGREEMENTS and
            set(contract['Q1_Q3_fields']) == {'question_id', 'exposure', 'value', 'evidence_ids'} and
            set(contract['Q2_fields']) == {'question_id', 'agreement', 'evidence_ids'},
            'question response contract changed')
    references = read(reference_path, decimals=True)
    require(references['schema_version'] == 'independent_passage_reader_references_v0.11',
            'unexpected reference schema')
    require(references['input_binding']['question_sha256'] == sha(question_path) and
            references['input_binding']['passage_pack_sha256'] == protocol['passage_pack_sha256'],
            'reference/question/passage binding mismatch')
    require(set(references['input_binding']['passage_sha256']) == EVIDENCE_IDS,
            'reference passage ID set changed')
    require([a['question_id'] for a in references['answers']] == QUESTION_IDS and
            [a['question_id'] for a in references['adjudications']] == QUESTION_IDS,
            'reference/adjudication order changed')
    for answer, adjudication in zip(references['answers'], references['adjudications']):
        require(adjudication['well_posed'] is True, 'reference question is not well posed')
        sets = adjudication['minimal_sufficient_passage_sets']
        require(isinstance(sets, list) and len(sets) > 0, 'missing sufficient evidence subsets')
        for evidence in sets:
            require(isinstance(evidence, list) and len(evidence) > 0 and
                    all(type(x) is str and x in EVIDENCE_IDS for x in evidence) and
                    len(set(evidence)) == len(evidence), 'invalid reference evidence subset')
        require(any(set(subset).issubset(answer['evidence_ids']) for subset in sets),
                'reference answer lacks sufficient evidence')
    return protocol, references


def compare_answer(answer, reference, adjudication):
    qid = reference['question_id']
    content_match = (answer['agreement'] == reference['agreement'] if qid == 'Q2' else
                     answer['exposure'] == reference['exposure'] and answer['value'] == reference['value'])
    cited = set(answer['evidence_ids'])
    alternatives = adjudication['minimal_sufficient_passage_sets']
    satisfied = [subset for subset in alternatives if set(subset).issubset(cited)]
    core_union = set().union(*(set(subset) for subset in alternatives))
    result = {'question_id': qid, 'status': 'scored', 'content_match': content_match,
              'evidence_sufficient': bool(satisfied), 'correct': content_match and bool(satisfied),
              'predicted_evidence_ids': answer['evidence_ids'],
              'sufficient_evidence_subsets': alternatives, 'satisfied_evidence_subsets': satisfied,
              'additional_valid_evidence_ids': [x for x in answer['evidence_ids'] if x not in core_union]}
    if qid == 'Q2':
        result.update(predicted_agreement=answer['agreement'], reference_agreement=reference['agreement'])
    else:
        result.update(predicted_exposure=answer['exposure'], reference_exposure=reference['exposure'],
                      predicted_value_decimal=None if answer['value'] is None else str(answer['value']),
                      reference_value_decimal=str(reference['value']),
                      numeric_comparison='Exact Decimal equality; zero tolerance; no bool coercion')
    return result


def failures(reason):
    return [{'question_id': qid, 'status': 'failed', 'failure_reason': reason,
             'content_match': False, 'evidence_sufficient': False, 'correct': False}
            for qid in QUESTION_IDS]


def score_condition(row, references):
    ident = row.get('item_id')
    result = {'condition_id': ident, 'schema_valid': False, 'failure': None, 'questions': []}
    try:
        require(set(row) == PROJECTION_FIELDS, 'exported response metadata field set differs')
        require(type(row['content']) is str, 'missing response content')
        require(row['content_sha256'] == hashlib.sha256(row['content'].encode()).hexdigest() and
                row['content_utf8_bytes'] == len(row['content'].encode()), 'content hash/byte mismatch')
        require(row['content_export_status'] == 'reviewed_unchanged_native_text',
                'generated content has not received export review')
        require(row['truncated'] is False and row['output_budget_reached'] is False and
                row['stop'] is True and row['stop_type'] in ('eos', 'word'),
                'native truncation, output limit, or incomplete stop retained as failure')
        require(type(row['tokens_predicted']) is int and 0 < row['tokens_predicted'] <= 384,
                'generated token count outside frozen budget')
        answers = parse_answers(row['content'])
        result['schema_valid'] = True
        result['questions'] = [compare_answer(answer, reference, adjudication)
                               for answer, reference, adjudication in zip(
                                   answers, references['answers'], references['adjudications'])]
    except (EvaluationInputError, KeyError, TypeError) as error:
        result['failure'] = str(error)
        result['questions'] = failures(str(error))
    result['correct_questions'] = sum(x['correct'] for x in result['questions'])
    result['question_count'] = 3
    return result


def evaluate(prediction_path=PREDICTIONS):
    result = {'schema_version': 'reader_evaluation_v0.11', 'status': 'started',
              'conditions': [], 'forward_passes': 0, 'repairs': 0,
              'unit_of_analysis': 'Each of three fixed related questions in each evidence-order condition',
              'interpretation': 'One selected current-document development example; no population accuracy or method comparison.',
              'numeric_policy': 'Exact Decimal numeric equality, zero tolerance, boolean rejected',
              'evidence_policy': 'A frozen sufficient subset is required; extra valid IDs reported separately.',
              'input_hashes': {'protocol': sha(PROTOCOL), 'reference': sha(REFERENCES),
                               'questions': sha(QUESTIONS)}}
    try:
        protocol, references = bindings()
        require(Path(prediction_path).is_file(), 'missing exported predictions; no output synthesized')
        result['input_hashes']['exported_predictions'] = sha(prediction_path)
        exported = read(prediction_path)
        require(isinstance(exported, dict) and set(exported) == {
            'schema_version', 'frozen_inputs_sha256', 'export_review_sha256', 'responses'},
                'export wrapper exact schema violated')
        require(exported['schema_version'] == 'native_reader_outputs_v0.11', 'wrong export wrapper version')
        require(exported['frozen_inputs_sha256'] == sha(FROZEN), 'frozen collector binding mismatch')
        require(exported['export_review_sha256'] == sha(EXPORT_REVIEW), 'export review hash mismatch')
        review = read(EXPORT_REVIEW)
        require(isinstance(review, dict) and set(review) == {
            'schema_version', 'frozen_inputs_sha256', 'responses'} and
            review['schema_version'] == 'native_reader_export_review_v0.11' and
            review['frozen_inputs_sha256'] == sha(FROZEN), 'export review binding/schema mismatch')
        frozen = read(FROZEN)
        require(frozen['request_sha256'] == protocol['request_sha256'] and
                frozen['execution_order'] == CONDITION_IDS, 'collector/request/protocol binding mismatch')
        rows = exported['responses']
        require(isinstance(rows, list) and len(rows) == 2 and all(isinstance(x, dict) for x in rows),
                'missing, excessive, or nonobject condition responses')
        require([x.get('item_id') for x in rows] == CONDITION_IDS, 'condition order/identity changed or duplicated')
        approvals = review['responses']
        require(isinstance(approvals, list) and len(approvals) == 2, 'missing export review decisions')
        for row, approval in zip(rows, approvals):
            require(isinstance(approval, dict) and set(approval) == {
                'item_id', 'content_sha256', 'allow_content_export', 'source_copy_budget_review'} and
                approval['item_id'] == row['item_id'] and
                approval['content_sha256'] == row['content_sha256'] and
                approval['allow_content_export'] is True and
                isinstance(approval['source_copy_budget_review'], str) and
                bool(approval['source_copy_budget_review'].strip()), 'response not bound to export review')
        result['conditions'] = [score_condition(row, references) for row in rows]
        result['status'] = 'completed_scoring'
        result['all_responses_schema_valid'] = all(x['schema_valid'] for x in result['conditions'])
        # Six answers are not independent samples. Deliberately avoid aggregate accuracy.
        result['format_failure_conditions'] = [x['condition_id'] for x in result['conditions']
                                                if not x['schema_valid']]
        result['between_condition_answer_changes'] = None
        result['between_condition_evidence_changes'] = None
        result['comparison_status'] = 'unavailable_due_to_response_schema_failure'
        if result['all_responses_schema_valid']:
            def content_signature(row):
                value = row.get('predicted_value_decimal')
                return (row.get('predicted_exposure'), None if value is None else Decimal(value),
                        row.get('predicted_agreement'))
            pairs = list(zip(QUESTION_IDS, result['conditions'][0]['questions'],
                             result['conditions'][1]['questions']))
            result['between_condition_answer_changes'] = [qid for qid, first, second in pairs
                if content_signature(first) != content_signature(second)]
            result['between_condition_evidence_changes'] = [qid for qid, first, second in pairs
                if set(first['predicted_evidence_ids']) != set(second['predicted_evidence_ids'])]
            result['comparison_status'] = 'available_for_this_fixed_example_only'
    except (EvaluationInputError, OSError, KeyError, TypeError) as error:
        result['status'] = 'input_validation_failed'
        result['failure'] = str(error)
        result['conditions'] = [{'condition_id': ident, 'schema_valid': False,
                                 'failure': str(error), 'questions': failures(str(error)),
                                 'correct_questions': 0, 'question_count': 3} for ident in CONDITION_IDS]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--predictions', type=Path, default=PREDICTIONS)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--check', action='store_true',
                        help='Recompute and exact-compare an existing result without writing any file')
    args = parser.parse_args()
    if args.check:
        require(args.output.is_file(), 'no existing scoring result to check')
    else:
        require(not args.output.exists(), 'refusing to overwrite a retained scoring result')
    result = evaluate(args.predictions)
    if args.check:
        require(read(args.output) == result, 'existing result differs from exact offline recomputation')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'mode': 'check_passed_no_writes' if args.check else 'created',
                      'output': str(args.output),
                      'conditions': [{'condition_id': x['condition_id'],
                                      'schema_valid': x['schema_valid'],
                                      'correct_questions': x['correct_questions']} for x in result['conditions']]}))


if __name__ == '__main__':
    main()

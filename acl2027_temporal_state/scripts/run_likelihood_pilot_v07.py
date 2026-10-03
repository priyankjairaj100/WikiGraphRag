#!/usr/bin/env python3
"""Replay pinned scorer measurements over unversioned frozen candidates.

This adapter never loads a model, generates text, or reads QA/gold. The two
predeclared label orders are both retained; no condition is selected by its
decoded outcome. Numerical scores are uncalibrated additive compatibility.
"""
import argparse
from collections.abc import Mapping
from dataclasses import asdict, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from temporal_state.bounded import claim_to_dict
from temporal_state.correction_io import (dump_correction_cache, load_correction_cache,
    make_correction_cache, policy_to_dict)
from temporal_state.decoder import Limits
from temporal_state.objective_pipeline import decode_objective
from temporal_state.scored_io import make_cache


METHODS = ('independent', 'iterative', 'iterative_restarts', 'joint_exact')
MODES = ('historical', 'active_support')
CONDITIONS = ('yes_no', 'no_yes')
SETTINGS = {'methods': list(METHODS), 'objective_modes': list(MODES),
    'limits': asdict(Limits()), 'restarts': 10, 'seed': 7, 'beta': 1,
    'penalties': [], 'overflow_policy': 'fail_no_pruning',
    'primary_condition': 'yes_no', 'diagnostic_condition': 'no_yes',
    'exact_gap_negative_tolerance': 1e-9}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return sha256(raw).hexdigest()


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(item) for item in value]
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite JSON number: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def non_score_payload(cache):
    problem = asdict(cache.base.problem)
    problem.pop('score_provenance')
    for mention in problem['mentions']:
        mention.pop('null_score')
        for reading in mention['readings']:
            reading.pop('unary_score')
    for link in problem['links']:
        link.pop('score')
    return {'problem_without_scores': problem,
        'claims': [{'mention_id': mid, 'reading_id': rid, 'claim': claim_to_dict(claim)}
                   for (mid, rid), claim in sorted(cache.base.claims.items())],
        'aliases': thaw(cache.base.aliases), 'policy': policy_to_dict(cache.policy),
        'policy_provenance': thaw(cache.provenance)}


def validate_mapping(parent, mapping):
    """Each explicit score target must occur exactly once; no implicit zeros."""
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError('Item mapping must be a nonempty object')
    mentions = {m.mention_id: m for m in parent.base.problem.mentions}
    links = {link.link_id: link for link in parent.base.problem.links}
    expected = {('reading', m.mention_id, r.reading_id)
        for m in mentions.values() for r in m.readings}
    expected |= {('no_link', mid, None) for mid in mentions}
    expected |= {('link', link.mention_id, link.link_id) for link in links.values()}
    targets = {}
    for item_id, row in mapping.items():
        if (not isinstance(item_id, str) or not item_id
                or not isinstance(row, dict)
                or set(row) != {'kind', 'mention_id', 'candidate_id', 'link_id'}):
            raise ValueError('Unexpected item mapping fields')
        kind, mid, rid, lid = (row[k] for k in ('kind', 'mention_id', 'candidate_id', 'link_id'))
        if not isinstance(mid, str) or mid not in mentions:
            raise ValueError('Unknown mapped mention')
        if kind == 'reading':
            if lid is not None or not any(r.reading_id == rid and not r.unresolved
                                          for r in mentions[mid].readings):
                raise ValueError('Mapped resolved reading is absent')
            target = ('reading', mid, rid)
        elif kind == 'unresolved':
            unresolved = [r for r in mentions[mid].readings if r.unresolved]
            if rid is not None or lid is not None or len(unresolved) != 1:
                raise ValueError('Unresolved mapping needs exactly one explicit candidate')
            target = ('reading', mid, unresolved[0].reading_id)
        elif kind == 'no_link':
            if rid is not None or lid is not None:
                raise ValueError('No-link mapping cannot name a reading or link')
            target = ('no_link', mid, None)
        elif kind == 'link':
            if (not isinstance(lid, str) or lid not in links
                    or links[lid].mention_id != mid or links[lid].reading_id != rid):
                raise ValueError('Mapped link has different source reading')
            target = ('link', mid, lid)
        else:
            raise ValueError('Unknown score mapping kind')
        if target in targets.values():
            raise ValueError('Duplicate mapped score target')
        targets[item_id] = target
    if set(targets.values()) != expected:
        raise ValueError('Missing scores for resolved/unresolved readings, links, or no-link')
    return targets


def derive(parent, mapping, scores, condition, scorer, prompt_sha256, lineage):
    if condition not in CONDITIONS:
        raise ValueError('Undeclared likelihood condition')
    if (parent.base.provenance['evidence_type'] != 'unversioned_model_scores'
            or parent.base.provenance['candidate_model_revision'] is not None):
        raise ValueError('This adapter requires the frozen unversioned parent candidate cache')
    problem = parent.base.problem
    if problem.beta != 1 or problem.penalties:
        raise ValueError('Likelihood pilot requires beta=1 and no penalties')
    if (any(r.violations for m in problem.mentions for r in m.readings)
            or any(link.violations for link in problem.links)):
        raise ValueError('No hidden penalty terms are supported in this pilot')
    targets = validate_mapping(parent, mapping)
    if not isinstance(scores, dict) or set(scores) != set(targets):
        raise ValueError('Missing or extra measured scores')
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in scores.values()):
        raise ValueError('Measured scores must be finite real numbers')
    by_target = {targets[item_id]: value for item_id, value in scores.items()}
    definition = (f'Pinned scorer next-token conditional natural log P(Yes|prompt) minus '
        f'log P(No|prompt), condition={condition}; full-vocabulary softmax; explicit '
        'resolved/unresolved unary, link and no-link judgments; beta=1, no penalties. '
        'Unversioned frozen candidates and policy; uncalibrated additive compatibility, '
        'not factual correctness probabilities.')
    child_problem = replace(problem,
        mentions=tuple(replace(m, readings=tuple(replace(r,
            unary_score=by_target['reading', m.mention_id, r.reading_id]) for r in m.readings),
            null_score=by_target['no_link', m.mention_id, None]) for m in problem.mentions),
        links=tuple(replace(link, score=by_target['link', link.mention_id, link.link_id])
                    for link in problem.links), score_provenance=definition)
    provenance = {'evidence_type': 'pinned_scorer_unversioned_candidates',
        'candidate_model_id': parent.base.provenance['candidate_model_id'],
        'candidate_model_revision': None, 'scorer_model_id': scorer['model_id'],
        'scorer_model_revision': scorer['model_sha256'], 'prompt_sha256': prompt_sha256,
        'config_sha256': digest(canonical(lineage)), 'score_definition': definition}
    child_base = make_cache(child_problem, parent.base.claims, thaw(parent.base.aliases), provenance)
    child = make_correction_cache(child_base, parent.policy, thaw(parent.provenance))
    if non_score_payload(child) != non_score_payload(parent):
        raise ValueError('Likelihood adapter changed non-score inference inputs or annotation provenance')
    counts = {kind: sum(row['kind'] == kind for row in mapping.values())
              for kind in ('reading', 'unresolved', 'link', 'no_link')}
    return child, counts


def replay(child, condition):
    rows = []
    for mode in MODES:
        mode_rows = []
        for method in METHODS:
            state = decode_objective(child, method, objective_mode=mode,
                limits=Limits(**SETTINGS['limits']), restarts=SETTINGS['restarts'], seed=SETTINGS['seed'])
            diagnostics = asdict(state.result.diagnostics)
            diagnostics.pop('elapsed_seconds')
            mode_rows.append({'condition_id': condition, 'method': method,
                'objective_mode': mode, 'objective_identity_sha256': state.objective_digest,
                'derived_correction_cache_sha256': child.digest,
                'objective_within_condition_and_mode_only': state.result.objective,
                'selected_reading_ids': dict(state.result.reading_ids),
                'selected_link_ids': dict(state.result.link_ids),
                'active_claim_ids': sorted(c.claim_id for c in state.memory.claims),
                'active_interval_envelopes': {cid: asdict(envelope)
                    for cid, envelope in sorted(state.memory.envelopes.items())},
                'restatement_components': [list(group) for group in state.result.restatement_components],
                'search_diagnostics_without_timing': diagnostics})
        exact = next(row for row in mode_rows if row['method'] == 'joint_exact')
        for row in mode_rows:
            gap = (exact['objective_within_condition_and_mode_only']
                   - row['objective_within_condition_and_mode_only'])
            if gap < -SETTINGS['exact_gap_negative_tolerance']:
                raise ValueError('Heuristic score exceeded exact optimum beyond declared tolerance')
            row['within_condition_and_mode_exact_gap'] = gap
        rows.extend(mode_rows)
    return rows


def compare_conditions(rows):
    primary = {(row['method'], row['objective_mode']): row
               for row in rows if row['condition_id'] == 'yes_no'}
    comparisons = []
    for row in rows:
        if row['condition_id'] != 'no_yes':
            continue
        ref = primary[row['method'], row['objective_mode']]
        readings = sorted(mid for mid in ref['selected_reading_ids']
            if row['selected_reading_ids'][mid] != ref['selected_reading_ids'][mid])
        links = sorted(mid for mid in ref['selected_link_ids']
            if row['selected_link_ids'][mid] != ref['selected_link_ids'][mid])
        active, ref_active = set(row['active_claim_ids']), set(ref['active_claim_ids'])
        envelope_changes = sorted(cid for cid in active | ref_active
            if row['active_interval_envelopes'].get(cid) != ref['active_interval_envelopes'].get(cid))
        comparisons.append({'method': row['method'], 'objective_mode': row['objective_mode'],
            'primary_condition': 'yes_no', 'diagnostic_condition': 'no_yes',
            'changed_reading_mentions': readings, 'changed_link_mentions': links,
            'new_active_claim_ids': sorted(active-ref_active),
            'removed_active_claim_ids': sorted(ref_active-active),
            'changed_interval_envelope_claim_ids': envelope_changes,
            'same_selected_readings': not readings, 'same_selected_links': not links,
            'same_active_claim_ids': active == ref_active,
            'same_active_interval_envelopes': not envelope_changes,
            'score_across_conditions_compared': False})
    if len(comparisons) != len(METHODS) * len(MODES):
        raise ValueError('Both conditions need every declared method and objective')
    return comparisons


def likelihood_diagnostics(measurements, mapping):
    """Unfiltered token-label mass and logodds diagnostics, not quality metrics."""
    rows = []
    for item in measurements['rows']:
        rows.append({'condition_id': item['condition_id'], 'item_id': item['item_id'],
            'kind': mapping[item['item_id']]['kind'], 'input_tokens': item['input_tokens'],
            'yes_logprob': item['yes_logprob'], 'no_logprob': item['no_logprob'],
            'score': item['yes_logprob'] - item['no_logprob'],
            'literal_yes_no_probability_mass': math.exp(item['yes_logprob']) + math.exp(item['no_logprob'])})
    rows.sort(key=lambda row: (row['condition_id'], row['item_id']))
    summaries = []
    for condition in CONDITIONS:
        for kind in ('reading', 'unresolved', 'link', 'no_link'):
            group = [row for row in rows if row['condition_id'] == condition and row['kind'] == kind]
            if not group:
                raise ValueError('Missing likelihood diagnostic kind')
            scores = [row['score'] for row in group]
            masses = [row['literal_yes_no_probability_mass'] for row in group]
            summaries.append({'condition_id': condition, 'kind': kind, 'items': len(group),
                'score_min': min(scores), 'score_max': max(scores), 'score_mean': math.fsum(scores)/len(scores),
                'label_mass_min': min(masses), 'label_mass_max': max(masses),
                'label_mass_mean': math.fsum(masses)/len(masses)})
    primary = {row['item_id']: row for row in rows if row['condition_id'] == 'yes_no'}
    differences = [{'item_id': row['item_id'], 'kind': row['kind'],
        'diagnostic_minus_primary_logodds': row['score'] - primary[row['item_id']]['score']}
        for row in rows if row['condition_id'] == 'no_yes']
    return {'rows': rows, 'summaries_by_condition_and_kind': summaries,
        'label_order_logodds_differences': differences,
        'filtered_measurements': 0, 'scores_renormalized': False,
        'interpretation': 'Literal label probability mass is a diagnostic, not confidence in factual truth.'}


def completion_settings(tokens):
    return {'prompt': tokens, 'n_predict': 1, 'temperature': -1.0, 'seed': 7,
        'n_probs': 256, 'post_sampling_probs': False, 'cache_prompt': True,
        'return_tokens': True, 'stream': False, 'repeat_penalty': 1.0,
        'presence_penalty': 0.0, 'frequency_penalty': 0.0,
        'logit_bias': [], 'samplers': ['temperature']}


def validate_execution_plan(request, plan):
    fields = {'schema_version', 'request_sha256', 'execution_order', 'reason',
              'previous_partial_rows', 'created_utc'}
    if (not isinstance(plan, dict) or set(plan) != fields
            or plan['schema_version'] != 'local_backend_execution_plan_v0.7'
            or plan['request_sha256'] != digest(canonical(request))):
        raise ValueError('Execution plan does not bind the frozen request')
    if (type(plan['previous_partial_rows']) is not int or plan['previous_partial_rows'] < 0
            or any(not isinstance(plan[key], str) or not plan[key].strip()
                   for key in ('reason', 'created_utc'))):
        raise ValueError('Execution plan needs explicit replanning provenance')
    primary_ids = [item['item_id'] for item in request['items'] if item['condition_id'] == 'yes_no']
    expected = [{'condition_id': condition, 'item_id': item_id}
                for item_id in primary_ids for condition in CONDITIONS]
    if plan['execution_order'] != expected:
        raise ValueError('Execution plan must pair both label orders in the unchanged primary item order')
    keys = [(row['condition_id'], row['item_id']) for row in expected]
    request_keys = [(item['condition_id'], item['item_id']) for item in request['items']]
    if len(set(keys)) != len(keys) or len(keys) != len(request_keys) or set(keys) != set(request_keys):
        raise ValueError('Execution plan does not cover every frozen request exactly once')
    return keys


def verify_response(response, prompt, label_ids):
    if (response.get('truncated') is not False or response.get('tokens_evaluated') != prompt['input_tokens']
            or response.get('tokens_predicted') != 1 or response.get('prompt') != prompt['rendered_prompt']):
        raise ValueError('Raw response does not preserve the complete one-token prompt evaluation')
    timings = response.get('timings', {})
    if (any(type(timings.get(key)) is not int for key in ('cache_n', 'prompt_n', 'predicted_n'))
            or timings['cache_n'] < 0 or timings['prompt_n'] < 1 or timings['predicted_n'] != 1
            or timings['cache_n'] + timings['prompt_n'] != prompt['input_tokens']):
        raise ValueError('Raw prefix reuse accounting does not cover the complete input token sequence')
    params = response.get('generation_settings', {})
    expected = {'seed': 7, 'temperature': 0.0, 'n_predict': 1, 'n_probs': 256,
        'post_sampling_probs': False, 'repeat_penalty': 1.0, 'presence_penalty': 0.0,
        'frequency_penalty': 0.0, 'logit_bias': [], 'samplers': ['temperature'],
        'grammar': '', 'lora': []}
    if any(params.get(key) != value for key, value in expected.items()):
        raise ValueError('Raw response generation settings differ from the fixed unconstrained protocol')
    distributions = response.get('completion_probabilities')
    if not isinstance(distributions, list) or len(distributions) != 1:
        raise ValueError('Expected one raw next-token distribution')
    entries = distributions[0].get('top_logprobs')
    if not isinstance(entries, list) or len(entries) != 256:
        raise ValueError('Expected the declared 256 returned vocabulary ranks')
    by_id = {}
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'id', 'token', 'bytes', 'logprob'}
                or type(entry['id']) is not int or entry['id'] < 0 or entry['id'] in by_id
                or type(entry['logprob']) not in (int, float)
                or not math.isfinite(entry['logprob']) or entry['logprob'] > 1e-6):
            raise ValueError('Malformed or duplicate raw vocabulary rank')
        by_id[entry['id']] = entry
    found = {}
    for label, token_id in label_ids.items():
        entry = by_id.get(token_id)
        if (entry is None or entry['token'] != label
                or entry['bytes'] != list(label.encode())):
            raise ValueError('Literal label is absent from the raw unconstrained distribution')
        found[label] = entry['logprob']
    return found


def verify_backend_evidence(paths, blobs, request, measurements):
    """Check retained execution evidence without a model, network or receipt paths."""
    config, binding, receipt, props, numerical = (strict_json(blobs[key]) for key in
        ('backend_config', 'binding', 'receipt', 'props', 'numerical_replay'))
    plan = strict_json(blobs['execution_plan'])
    expected_keys = validate_execution_plan(request, plan)
    environment = strict_json(blobs['backend_environment'])
    if any(key not in environment or environment[key] is not None
           for key in ('LLAMA_ARG_CACHE_REUSE', 'LLAMA_ARG_SAMPLING_BACKEND')):
        raise ValueError('Recorded environment audit contains a cache-reuse or backend-sampling override')
    prompts = [strict_json(line) for line in blobs['rendered_prompts'].splitlines() if line.strip()]
    responses = [strict_json(line) for line in blobs['raw_responses'].splitlines() if line.strip()]
    if receipt.get('schema_version') != 'local_backend_scoring_receipt_v0.7' or receipt.get('status') != 'completed':
        raise ValueError('Only a completed recorded backend execution can be replayed')
    if binding.get('schema_version') != 'local_backend_binding_v0.7':
        raise ValueError('Unsupported backend binding schema')
    for obj in (binding, receipt):
        if obj.get('execution_plan_sha256') != digest(blobs['execution_plan']):
            raise ValueError('Backend run does not bind the declared paired execution plan')
    if binding.get('execution_order') != plan['execution_order']:
        raise ValueError('Backend binding order differs from declared execution plan')
    source_receipt = strict_json(blobs['collector_source_receipt'])
    source_hash = digest(blobs['executed_collector'])
    if (source_receipt.get('schema_version') != 'executed_collector_source_v0.7'
            or source_receipt.get('sha256') != source_hash
            or receipt.get('collector_script_sha256') != source_hash):
        raise ValueError('Executed collector source snapshot does not match its recorded execution receipt')
    canonical_binding_hash = digest(canonical(binding))
    if (receipt.get('binding_sha256') != canonical_binding_hash
            or measurements['backend_binding_sha256'] != canonical_binding_hash):
        raise ValueError('Backend canonical binding mismatch')
    file_bindings = {'request_file_sha256': 'request', 'backend_config_sha256': 'backend_config',
        'binding_file_sha256': 'binding', 'measurement_sha256': 'measurements',
        'raw_measurements_sha256': 'raw_responses', 'rendered_prompts_sha256': 'rendered_prompts'}
    if any(receipt.get(field) != digest(blobs[key]) for field, key in file_bindings.items()):
        raise ValueError('Backend receipt file hash mismatch')
    for field in ('model_id', 'model_sha256', 'model_repo_revision', 'runtime_commit', 'runtime_release'):
        if receipt.get(field) != config.get(field) or binding.get(field) != config.get(field):
            raise ValueError('Scorer asset or runtime revision differs from frozen configuration')
    if (receipt.get('request_sha256') != digest(canonical(request))
            or binding.get('request_sha256') != digest(canonical(request))
            or binding.get('backend_config_sha256') != digest(blobs['backend_config'])):
        raise ValueError('Backend request or configuration binding mismatch')
    if (binding.get('server_props_sha256') != digest(blobs['props'])
            or binding.get('rendered_prompts_sha256') != digest(blobs['rendered_prompts'])
            or binding.get('softmax_evidence_sha256') != digest(blobs['softmax_evidence'])):
        raise ValueError('Backend supporting evidence file hash mismatch')
    if ('supplementary_vocab_evidence_sha256' in receipt
            and receipt['supplementary_vocab_evidence_sha256'] != digest(blobs['vocab_fallback_evidence'])):
        raise ValueError('Optional postrun vocabulary audit hash mismatch')
    template_hash = digest(props['chat_template'].encode())
    if receipt.get('chat_template_sha256') != template_hash or binding.get('chat_template_sha256') != template_hash:
        raise ValueError('Chat template binding mismatch')
    runtime_hash = digest(canonical(config['runtime_files']))
    executable = next(row['sha256'] for row in config['runtime_files'] if row['path'] == 'llama-server')
    if (receipt.get('runtime_files_sha256') != runtime_hash or binding.get('runtime_files_sha256') != runtime_hash
            or binding.get('runtime_executable_sha256') != executable
            or props.get('build_info') != config['runtime_release'] + '-' + config['runtime_commit'][:9]):
        raise ValueError('Runtime file or observed build binding mismatch')
    execution = receipt.get('execution', {})
    if (execution.get('runtime_files') != config['runtime_files']
            or execution.get('runtime_files_sha256') != runtime_hash
            or execution.get('runtime_executable_sha256') != executable):
        raise ValueError('Execution asset manifest differs from frozen runtime configuration')
    if execution.get('assets') != [{**row, 'verified_sha256': row['sha256']} for row in config['assets']]:
        raise ValueError('Executed model/runtime asset verification differs from configuration')
    if any(binding.get(key) is not True for key in ('full_vocab_logprobs', 'exact_label_prefix_verified',
            'context_untruncated', 'no_logit_bias', 'labels_unconstrained')):
        raise ValueError('Missing backend protocol assurances')
    if binding.get('completion_settings') != completion_settings([]):
        raise ValueError('Backend completion settings differ from fixed protocol')
    if config.get('cache_prompt') is not True:
        raise ValueError('This recorded pilot expects the fixed prefix-reuse backend configuration')
    for field in ('threads', 'batch_tokens', 'microbatch_tokens', 'address_space_limit_bytes'):
        if binding.get(field) != config.get(field):
            raise ValueError('Backend resource setting differs from frozen configuration')
    context = binding.get('context_tokens')
    if (type(context) is not int or not 1 <= context <= config['maximum_context_tokens']
            or context > request['max_context_tokens'] or execution.get('context_tokens') != context):
        raise ValueError('Backend context budget differs from recorded limits')
    if (config.get('cpu_seconds_limit') != 18000 or receipt.get('cpu_seconds_limit') != 18000
            or execution.get('cpu_seconds_limit') != config['cpu_seconds_limit']):
        raise ValueError('Fresh complete run does not use the declared CPU-time guard')
    command = execution.get('command', [])
    flags = {'--host': '127.0.0.1', '-c': str(context), '-t': str(config['threads']),
        '-tb': str(config['threads']), '-b': str(config['batch_tokens']), '-ub': str(config['microbatch_tokens']),
        '-ngl': '0', '--parallel': '1', '-ctk': config['kv_cache_key_type'],
        '-ctv': config['kv_cache_value_type'], '-fa': config['flash_attention'],
        '--chat-template-kwargs': '{"enable_thinking":false}'}
    if (not isinstance(command, list) or not command or Path(command[0]).name != 'llama-server'
            or any(command.count(flag) != 1 or command.index(flag) + 1 >= len(command)
                   or command[command.index(flag) + 1] != value for flag, value in flags.items())
            or any(flag not in command for flag in ('--no-context-shift', '--jinja'))
            or execution.get('address_space_limit_bytes') != config['address_space_limit_bytes']):
        raise ValueError('Recorded execution flags differ from the pinned CPU/precision/resource configuration')
    for rows in (prompts, responses):
        if [(row.get('condition_id'), row.get('item_id')) for row in rows] != expected_keys:
            raise ValueError('Raw backend records do not cover the exact declared execution plan order')
    if receipt.get('completed_rows') != len(expected_keys) or receipt.get('preflight_prompt_count') != len(expected_keys):
        raise ValueError('Backend receipt does not account for all prepared prompts')
    label_ids = binding.get('label_token_ids')
    if not isinstance(label_ids, dict) or set(label_ids) != {'Yes', 'No'}:
        raise ValueError('Missing explicit backend label tokens')
    measurement_by_key = {(row['condition_id'], row['item_id']): row for row in measurements['rows']}
    request_by_key = {(item['condition_id'], item['item_id']): item for item in request['items']}
    for key, prompt, raw in zip(expected_keys, prompts, responses):
        item = request_by_key[key]
        messages = item['messages']
        if len(messages) != 2 or [m['role'] for m in messages] != ['system', 'user']:
            raise ValueError('Only the declared two-message source-only prompt is supported')
        # Independent expansion of this template's no-tools, no-thinking branch;
        # no general template evaluator or model/tokenizer is invoked on replay.
        expected_prompt = ''.join('<|im_start|>' + m['role'] + '\n' + m['content'] + '<|im_end|>\n'
                                  for m in messages) + '<|im_start|>assistant\n<think>\n\n</think>\n\n'
        tokens = prompt['input_token_ids']
        if (prompt['rendered_prompt'] != expected_prompt
                or prompt['rendered_prompt_sha256'] != digest(expected_prompt.encode())
                or prompt['messages_sha256'] != digest(canonical(messages))
                or prompt['input_token_ids_sha256'] != digest(canonical(tokens))
                or not isinstance(tokens, list) or any(type(t) is not int or t < 0 for t in tokens)
                or prompt['input_tokens'] != len(tokens) or not 1 <= len(tokens) < context):
            raise ValueError('Recorded prompt rendering, complete token sequence or context length is inconsistent')
        if prompt.get('label_appended_token_ids') != {label: tokens + [token_id] for label, token_id in label_ids.items()}:
            raise ValueError('Recorded label append changes prompt tokenization')
        if raw.get('request') != completion_settings(tokens):
            raise ValueError('Raw model request differs from complete tokenized prompt or fixed settings')
        measured = measurement_by_key[key]
        for field in ('messages_sha256', 'rendered_prompt_sha256', 'input_token_ids_sha256', 'input_tokens'):
            if measured[field] != prompt[field]:
                raise ValueError('Normalized measurement differs from retained prompt evidence')
        probabilities = verify_response(raw['response'], prompt, label_ids)
        for label in ('Yes', 'No'):
            if (measured[label.lower() + '_token_id'] != label_ids[label]
                    or measured[label.lower() + '_logprob'] != probabilities[label]):
                raise ValueError('Normalized likelihood differs from verbatim backend response')
    counts = [p['input_tokens'] for p in prompts]
    for obj in (binding, receipt):
        if obj.get('input_tokens_min') != min(counts) or obj.get('input_tokens_max') != max(counts):
            raise ValueError('Backend input token range mismatch')
    if numerical.get('request') != completion_settings(prompts[0]['input_token_ids']):
        raise ValueError('Numerical replay did not repeat the first primary prompt')
    replay_probs = verify_response(numerical['response'], prompts[0], label_ids)
    first = measurement_by_key[expected_keys[0]]
    errors = {label: abs(replay_probs[label] - first[label.lower() + '_logprob']) for label in ('Yes', 'No')}
    expected_numerical = {'absolute_errors': errors, 'tolerance': 1e-6,
        'passed': all(value <= 1e-6 for value in errors.values()),
        'tokens_evaluated': prompts[0]['input_tokens'], 'truncated': False}
    if (receipt.get('rerun_absolute_tolerance') != 1e-6
            or receipt.get('numerical_replay') != expected_numerical):
        raise ValueError('Numerical replay report differs from retained responses or fixed tolerance')
    return {'model_id': binding['model_id'], 'model_sha256': binding['model_sha256'],
        'model_repo_revision': binding['model_repo_revision'], 'backend_binding_sha256': canonical_binding_hash,
        'raw_records_verified': len(prompts), 'numerical_replay': expected_numerical,
        'new_model_executions': 0,
        'validation_scope': 'Retained-byte consistency and recorded execution evidence; replay does not independently retokenize or rerun the external model asset.'}


def main():
    import prepare_likelihood_scoring_v07 as protocol
    folder = ROOT / 'data/likelihood_scoring_v07'
    old = ROOT / 'data/natural_model_pilot_v06'
    defaults = {'source_request': old / 'candidate_request.json', 'candidates': old / 'candidate_output_repaired.json',
        'parent_cache': old / 'shared_cache.json', 'parent_manifest': old / 'adapter_manifest.json',
        'request': folder / 'request.json', 'manifest': folder / 'manifest.json',
        'measurements': folder / 'measurements.json', 'binding': folder / 'backend_binding.json',
        'backend_config': ROOT / 'configs/local_backend_v07.json',
        'receipt': ROOT / 'results/local_backend_v07_scoring_receipt.json',
        'props': folder / 'server_props.json', 'rendered_prompts': folder / 'rendered_prompts.jsonl',
        'raw_responses': folder / 'raw_backend_responses.jsonl',
        'numerical_replay': folder / 'numerical_replay_raw.json',
        'softmax_evidence': ROOT / 'results/local_backend_v07_softmax_evidence.txt',
        'vocab_fallback_evidence': ROOT / 'results/local_backend_v07_vocab_fallback_evidence.txt',
        'backend_environment': ROOT / 'results/local_backend_v07_environment.json',
        'execution_plan': folder / 'execution_plan.json',
        'executed_collector': folder / 'executed_collector.py.txt',
        'collector_source_receipt': ROOT / 'results/local_backend_v07_collector_source_receipt.json'}
    parser = argparse.ArgumentParser(description=__doc__)
    for key, path in defaults.items():
        parser.add_argument('--' + key.replace('_', '-'), type=Path, default=path)
    parser.add_argument('--cache-dir', type=Path, default=folder / 'derived')
    parser.add_argument('--result', type=Path, default=ROOT / 'results/likelihood_pilot_v07.json')
    args = parser.parse_args()
    paths = {key: getattr(args, key) for key in defaults}
    blobs = {key: path.read_bytes() for key, path in paths.items()}
    # These are unsuccessful setup attempts, not alternative measured scores.
    # Their bytes remain visible in child lineage rather than being overwritten.
    failed_paths = [ROOT / 'results/local_backend_v07_smoke_sandbox_failure.json',
        ROOT / 'results/local_backend_v07_smoke_sandbox_failure.server.log',
        ROOT / 'results/local_backend_v07_scoring_f16kv_failure_receipt.json',
        ROOT / 'results/local_backend_v07_scoring_f16kv_failure.server.log',
        ROOT / 'results/local_backend_v07_config_before_q8_kv.json']
    failed_paths += [folder / 'attempt_cache_false' / name for name in (
        'local_backend_v07.json', 'local_backend_v07_scoring_receipt.json',
        'local_backend_v07_scoring.server.log', 'backend_binding.json', 'server_props.json',
        'rendered_prompts.jsonl', 'raw_backend_responses.jsonl', 'interruption_receipt.json')]
    if any(not path.is_file() for path in failed_paths):
        raise ValueError('A declared unsuccessful setup artifact is missing')
    failed_attempt_hashes = {str(path.relative_to(ROOT)): digest(path.read_bytes())
                            for path in failed_paths}
    interrupted = strict_json((folder / 'attempt_cache_false/interruption_receipt.json').read_bytes())
    if (interrupted.get('completed_score_rows') != 0
            or (folder / 'attempt_cache_false/raw_backend_responses.jsonl').read_bytes() != b''):
        raise ValueError('Interrupted setup unexpectedly contains scoring responses')
    failed_by_name = {str(path.relative_to(ROOT)): path for path in failed_paths}
    for record in interrupted['preserved_files']:
        # Resolve only from explicit known paths, never from an untrusted receipt.
        path = failed_by_name.get(record['path'])
        if (path is None or failed_attempt_hashes[record['path']] != record['sha256']
                or path.stat().st_size != record['bytes']):
            raise ValueError('Interrupted setup preservation hash mismatch')
    partial_folder = folder / 'attempt_cpu7200'
    partial_paths = [partial_folder / name for name in (
        'local_backend_v07.json', 'local_backend_v07.py', 'local_backend_v07_scoring_receipt.json',
        'local_backend_v07_scoring.server.log', 'backend_binding.json', 'server_props.json',
        'rendered_prompts.jsonl', 'raw_backend_responses.jsonl', 'executed_collector.py.txt',
        'local_backend_v07_collector_source_receipt.json', 'local_backend_v07_cpu_guard_change.json',
        'interruption_receipt.json')]
    if any(not path.is_file() for path in partial_paths):
        raise ValueError('A declared interrupted CPU7200 run artifact is missing')
    partial_by_name = {str(path.relative_to(ROOT)): path for path in partial_paths}
    partial_hashes = {name: digest(path.read_bytes()) for name, path in partial_by_name.items()}
    partial_interruption = strict_json((partial_folder / 'interruption_receipt.json').read_bytes())
    partial_raw = [strict_json(row) for row in (partial_folder / 'raw_backend_responses.jsonl').read_bytes().splitlines()
                   if row.strip()]
    execution_plan = strict_json(blobs['execution_plan'])
    if (partial_interruption.get('status') != 'interrupted_for_resource_replanning'
            or partial_interruption.get('completed_score_rows') != len(partial_raw)
            or execution_plan['previous_partial_rows'] != len(partial_raw)):
        raise ValueError('Interrupted CPU7200 run count differs from the replanning record')
    for record in partial_interruption['preserved_files']:
        path = partial_by_name.get(record['path'])
        if (path is None or partial_hashes[record['path']] != record['sha256']
                or path.stat().st_size != record['bytes']):
            raise ValueError('Interrupted CPU7200 run preservation hash mismatch')
    request, manifest, measurements = (strict_json(blobs[key]) for key in ('request', 'manifest', 'measurements'))
    preparation = protocol.validate_prepared(strict_json(blobs['source_request']),
        blobs['candidates'].decode(), request, manifest)
    measurement_validation = protocol.validate_measurements(measurements, request,
        backend_binding_sha256=digest(canonical(strict_json(blobs['binding']))))
    backend_validation = verify_backend_evidence(paths, blobs, request, measurements)
    parent = load_correction_cache(paths['parent_cache'])
    # Parent source/candidate binding is not inferred from opaque item IDs.
    parent_lineage = strict_json(blobs['parent_manifest'])
    parent_configuration = parent_lineage['configuration']
    parent_inputs = parent_configuration['input_binding']['files']
    if (parent_lineage['shared_correction_cache_sha256'] != parent.digest
            or parent_lineage['base_cache_sha256'] != parent.base.digest
            or parent_lineage['configuration_sha256'] != digest(canonical(parent_configuration))
            or parent.base.provenance['config_sha256'] != digest(canonical(parent_configuration))
            or parent_inputs['candidates']['sha256'] != digest(blobs['candidates'])
            or parent_inputs['candidate_request']['sha256'] != digest(blobs['source_request'])
            or digest(blobs['candidates']) != manifest['candidate_raw_output_sha256']):
        raise ValueError('Frozen parent cache lineage does not bind these source and candidate bytes')
    scores_by_condition = {condition: {} for condition in CONDITIONS}
    for row in measurement_validation['scores']:
        scores_by_condition[row['condition_id']][row['item_id']] = row['score']
    lineage_common = {'schema_version': 'likelihood_cache_lineage_v0.7',
        'input_file_sha256': {key: digest(raw) for key, raw in sorted(blobs.items())},
        'parent_correction_cache_sha256': parent.digest, 'parent_base_cache_sha256': parent.base.digest,
        'parent_score_provenance': thaw(parent.base.provenance),
        'settings': SETTINGS, 'runner_sha256': digest(Path(__file__).read_bytes()),
        'unchanged_non_score_inference_payload_sha256': digest(canonical(non_score_payload(parent))),
        'backend_validation': backend_validation, 'new_model_executions': 0}
    lineage_common['preserved_unsuccessful_setup_attempt_file_sha256'] = failed_attempt_hashes
    lineage_common['setup_change_scope'] = ('Loopback sandbox failure, F16 KV memory-limit failure and '
        'cache-disabled interrupted prefill were retained. Q8 KV and fixed prefix reuse were configured '
        'before any completed scoring response; no alternative measurement was selected by score.')
    lineage_common['preserved_interrupted_cpu7200_attempt_file_sha256'] = partial_hashes
    lineage_common['execution_replanning'] = {'previous_partial_rows_preserved': len(partial_raw),
        'partial_rows_used_as_final_scores': 0, 'execution_plan_file_sha256': digest(blobs['execution_plan']),
        'scope': 'A fresh complete paired-order run replaced the interrupted CPU7200 attempt for resource reasons. '
                 'Frozen request and candidate bytes are unchanged; this execution-order decision occurred after '
                 'a partial run and is not an ex ante benchmark preregistration.'}
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    rows, cache_records = [], []
    for condition in CONDITIONS:
        lineage = {**lineage_common, 'condition_id': condition}
        child, counts = derive(parent, manifest['item_mapping'], scores_by_condition[condition], condition,
            backend_validation, request['prompt_sha256'], lineage)
        cache_path = args.cache_dir / (condition + '_cache.json')
        dump_correction_cache(child, cache_path)
        record = {'lineage': lineage, 'derived_correction_cache_sha256': child.digest,
            'derived_base_cache_sha256': child.base.digest, 'derived_cache_file_sha256': digest(cache_path.read_bytes()),
            'configuration_sha256': child.base.provenance['config_sha256'], 'replaced_score_counts': counts,
            'non_score_inputs_and_annotation_provenance_identical': True}
        (args.cache_dir / (condition + '_lineage.json')).write_bytes(canonical(record) + b'\n')
        cache_records.append(record)
        rows.extend(replay(child, condition))
    if any(paths[key].read_bytes() != raw for key, raw in blobs.items()):
        raise ValueError('An input artifact changed during replay')
    comparisons = compare_conditions(rows)
    result = {'schema_version': 'likelihood_decoder_replay_v0.7', 'settings': SETTINGS,
        'preparation_validation': preparation, 'measurement_validation': measurement_validation,
        'backend_validation': backend_validation, 'derived_caches': cache_records, 'runs': rows,
        'primary_vs_label_order_diagnostic': comparisons,
        'likelihood_diagnostics': likelihood_diagnostics(measurements, manifest['item_mapping']),
        'number_of_decoder_replays': len(rows), 'qa_or_gold_loaded': False, 'accuracy_measured': False,
        'new_model_executions': 0, 'model_measurements_replayed': len(measurements['rows']),
        'condition_selected_using_results': False, 'timings_omitted': True,
        'limits': ['One development history with unversioned candidates and one resolved reading per mention.',
            'Conditional literal-label likelihoods are uncalibrated additive scores, not factual truth probabilities.',
            'No-link scores apply even when the selected reading is unresolved; no state-conditional fallback model.',
            'Exact gaps measure optimization only; no accuracy, candidate recall or joint inference benefit is established.',
            'Quantized 0.6B CPU scoring is a distinct backend from the unrun prepared 8B baseline.',
            'Ordinal-v0.6 comparisons confound model, prompt and score semantics; no benefit is attributed to likelihood scoring.',
            'Offline artifact replay is not another model execution; external assets are needed for a new model run.']}
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_bytes(canonical(result) + b'\n')
    print(json.dumps({'decoder_replays': len(rows), 'measurements': len(measurements['rows']),
        'conditions': len(CONDITIONS), 'changed_condition_method_mode_pairs': sum(
            not all(row[key] for key in ('same_selected_readings', 'same_selected_links',
            'same_active_claim_ids', 'same_active_interval_envelopes')) for row in comparisons),
        'numerical_replay_passed': backend_validation['numerical_replay']['passed'],
        'qa_or_gold_loaded': False, 'new_model_executions': 0}))


if __name__ == '__main__':
    main()

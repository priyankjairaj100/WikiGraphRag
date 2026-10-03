"""Source-only migration validation and explicit prefix-supported alias routing.

This module never reads questions' answers or scoring labels. Exact spans and
hashes prove provenance consistency, not that a model's interpretation is true.
"""
from dataclasses import dataclass, fields
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata

from .bounded import TemporalClaim, claim_from_dict, build_bounded_memory
from .models import Assertion, Prediction, validate_date


@dataclass(frozen=True)
class DerivedPass:
    claims: tuple[TemporalClaim, ...]
    aliases: tuple[dict, ...]
    metadata: dict


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _object_from_file(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    data = json.loads(Path(path).read_bytes().decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
    if not isinstance(data, dict):
        raise ValueError('Expected JSON object')
    return data


def validate_aliases(aliases, claims, sources, history_by_source=None):
    by_source = {s.source_id: s for s in sources}
    subject_histories = {}
    for claim in claims:
        subject_histories.setdefault(claim.subject, set()).add(
            history_by_source[claim.source_id] if history_by_source else None)
    for row in aliases:
        if set(row) != {'subject', 'alias', 'source_id', 'evidence_spans'}:
            raise ValueError('Alias fields differ from the migration schema')
        if row['subject'] not in subject_histories:
            raise ValueError('Alias must reference a predicted subject')
        if not isinstance(row['alias'], str) or not row['alias'].strip():
            raise ValueError('Alias must be nonempty text')
        if row['source_id'] not in by_source:
            raise ValueError('Alias source is outside the supplied prefix')
        if history_by_source and history_by_source[row['source_id']] not in subject_histories[row['subject']]:
            raise ValueError('Alias source belongs to another history')
        if not isinstance(row['evidence_spans'], list) or not row['evidence_spans']:
            raise ValueError('Alias requires source evidence')
        for span in row['evidence_spans']:
            if set(span) != {'start', 'end', 'quote'}:
                raise ValueError('Unexpected alias evidence fields')
            start, end, quote = span['start'], span['end'], span['quote']
            if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(by_source[row['source_id']].text)
                or not isinstance(quote, str) or not quote
                or by_source[row['source_id']].text[start:end] != quote):
                raise ValueError('Alias evidence does not match source offsets')


def load_migration(path, prefix, raw_path, prompt_path):
    data = _object_from_file(path)
    expected = {'schema_version', 'pass_id', 'provenance_kind', 'backend',
                'prefix_input_sha256', 'raw_extraction_sha256', 'prompt_sha256',
                'claims', 'aliases', 'change_log', 'limitations'}
    if set(data) != expected or data['schema_version'] != '0.3':
        raise ValueError('Migration top-level fields differ from version 0.3')
    if (data['prefix_input_sha256'] != prefix.input_sha256
        or data['raw_extraction_sha256'] != file_sha(raw_path)
        or data['prompt_sha256'] != file_sha(prompt_path)):
        raise ValueError('Migration input/prompt digest mismatch')
    raw = _object_from_file(raw_path)
    if raw['input_sha256'] != prefix.input_sha256 or raw['pass_id'] != data['pass_id']:
        raise ValueError('Raw extraction and migration prefixes or pass IDs differ')
    if data['provenance_kind'] != 'source_only_model_assisted_migration':
        raise ValueError('Unsupported migration provenance')
    if data['backend'] != {'model_revision': None, 'decoding_settings': None,
                           'pinned': False, 'kind': 'conversational_model_agent'}:
        raise ValueError('Migration backend metadata differs from actual protocol')
    for key in ['claims', 'aliases', 'change_log', 'limitations']:
        if not isinstance(data[key], list):
            raise ValueError(f'{key} must be an array')
    if not data['claims']:
        raise ValueError('Empty migration')
    claim_fields = {field.name for field in fields(TemporalClaim)}
    if any(not isinstance(row,dict) or set(row) != claim_fields for row in data['claims']):
        raise ValueError('Every migration claim must preserve the complete explicit schema')
    claims = tuple(claim_from_dict(row) for row in data['claims'])
    raw_by_id = {a['assertion_id']: a for a in raw['assertions']}
    history = {sid: meta['history_id'] for sid, meta in prefix.metadata.items()}
    covered = set()
    for claim in claims:
        if claim.raw_assertion_id not in raw_by_id:
            raise ValueError('Derived claim lacks a known raw assertion')
        original = raw_by_id[claim.raw_assertion_id]
        if claim.source_id != original['source_id'] or claim.source_id not in history:
            raise ValueError('Migration changed or escaped its assertion source')
        if any(sid not in history or history[sid] != history[claim.source_id]
               for sid in claim.context_source_ids):
            raise ValueError('Migration context escapes its own history prefix')
        if claim.reported_at and claim.reported_at > prefix.prefixes[history[claim.source_id]]:
            raise ValueError('Report date exceeds the permitted prefix')
        if not claim.derivation_note.strip():
            raise ValueError('Derived claims require semantic provenance')
        covered.add(claim.raw_assertion_id)
    # Exclusions need an explicit typed record; all current passes preserve rows.
    for item in data['change_log']:
        if isinstance(item, dict) and item.get('action') == 'exclude':
            if item.get('raw_assertion_id') not in raw_by_id or not item.get('reason'):
                raise ValueError('Exclusion must identify a raw assertion and reason')
            covered.add(item['raw_assertion_id'])
    if covered != set(raw_by_id):
        raise ValueError('Some raw assertions disappeared without a logged exclusion')
    # Global maximum validates all source references/spans; per-history gating
    # above prevents this validation cutoff from granting a longer input prefix.
    build_bounded_memory(prefix.sources, claims, max(prefix.prefixes.values()))
    validate_aliases(data['aliases'], claims, prefix.sources, history)
    return DerivedPass(claims, tuple(data['aliases']), data)


def _tokens(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = re.sub(r'\bceo\b', 'chief executive officer', text)
    text = re.sub(r'\bcfo\b', 'chief financial officer', text)
    return set(re.findall(r'[^\W_]+', text)) - {'the', 'a', 'an', 'of', 'is', 'was'}


def route_question(text, claims, aliases, sources, cutoff, *, use_aliases=True):
    """Return all matching predicted keys; alias collisions stay ambiguous.

    This is lexical routing over model-supplied, span-backed alias records, not
    semantic retrieval or gold-key routing. No longest-name tie-break merges
    colliding subjects. Exact canonical subject text remains a baseline route.
    """
    validate_date(cutoff)
    available = {s.source_id for s in sources if s.available_at <= cutoff}
    eligible = [c for c in claims if c.source_id in available
                and all(sid in available for sid in c.context_source_ids)]
    subject_aliases = {}
    if use_aliases:
        for row in aliases:
            if row['source_id'] in available:
                subject_aliases.setdefault(row['subject'], []).append(row['alias'])
    query_tokens = _tokens(text)
    keys = set()
    for claim in eligible:
        relation, scope = _tokens(claim.relation), _tokens(claim.scope)
        if not relation <= query_tokens or not scope <= query_tokens:
            continue
        names = [claim.subject, *subject_aliases.get(claim.subject, [])]
        if any(_tokens(name) and _tokens(name) <= query_tokens for name in names):
            keys.add(claim.key)
    return tuple(sorted(keys))


def answer_question(memory, question, claims, aliases, sources, *, use_aliases=True,
                    query_mode='announced_schedule'):
    keys = route_question(question.text, claims, aliases, sources,
                          question.information_cutoff, use_aliases=use_aliases)
    if len(keys) != 1:
        return Prediction(question.question_id, 'abstain' if not keys else 'indeterminate',
            diagnostics={'reason':'no_predicted_key' if not keys else 'ambiguous_predicted_key',
                         'candidate_keys':keys,'routing':'prefix_supported_aliases' if use_aliases else 'canonical_lexical'})
    prediction = memory.answer_key(question, keys[0], query_mode=query_mode)
    prediction.diagnostics['routing'] = 'prefix_supported_aliases' if use_aliases else 'canonical_lexical'
    return prediction


def exact_projection(claims):
    """Deliberately lose observations/bounds for the matched exact-date baseline.

    Same migrated evidence and lexical labels, never a conversion of a state
    observation into an exact appointment. Negative records are excluded because
    the v0.1 positive-occupancy API cannot represent them. Targets are not passed.
    The old baseline's documented open-end persistence remains unchanged.
    """
    records, excluded = [], []
    for c in claims:
        if c.polarity == 'negative':
            excluded.append(c.claim_id)
            continue
        start = c.start.lower if c.start.lower == c.start.upper else None
        end = c.end.lower if c.end.lower == c.end.upper else None
        unresolved = c.modality in {'conditional','uncertain'} or c.operation == 'UNRESOLVED'
        records.append(Assertion(c.claim_id,c.source_id,c.subject,c.relation,c.value,start,end,c.scope,
            'UNRESOLVED' if unresolved else 'ASSERT',None,c.evidence_spans[0].quote,
            'exact_projection_of_migrated_source_only_claim',c.context_source_ids))
    return records, excluded

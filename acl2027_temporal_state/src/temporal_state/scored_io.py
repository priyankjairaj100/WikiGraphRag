"""Immutable, question-free candidate/score cache shared by every decoder.

Hashes certify artifact consistency, not provenance truth or source entailment.
The cache binds each resolved reading to its full bounded temporal claim; the
decoder's exact dates are only a checked projection of those richer bounds.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from types import MappingProxyType

from .bounded import (DayBounds, EvidenceSpan, TemporalClaim, build_bounded_memory,
                      claim_from_dict, claim_to_dict, normalize_key)
from .decoder import Link, Limits, Mention, Penalty, Problem, Reading, _Engine
from .models import Source
from .representation_io import validate_aliases


SCHEMA_VERSION = "0.4"
UNVERSIONED_SCHEMA_VERSION = "0.6"
PINNED_SCORER_SCHEMA_VERSION = "0.7"
_LEGACY_EVIDENCE_TYPES = {"authored_diagnostic", "migration_singleton_smoke", "pinned_model_scores"}
_UNVERSIONED_EVIDENCE_TYPE = "unversioned_model_scores"
_PINNED_SCORER_EVIDENCE_TYPE = "pinned_scorer_unversioned_candidates"
_PROVENANCE_FIELDS = {
    "evidence_type", "candidate_model_id", "candidate_model_revision",
    "scorer_model_id", "scorer_model_revision", "prompt_sha256",
    "config_sha256", "score_definition",
}


@dataclass(frozen=True)
class FrozenScoredInput:
    problem: Problem
    claims: Mapping
    aliases: tuple
    provenance: Mapping
    digest: str


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _strict_object(value, expected, where):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"Unexpected or missing fields in {where}")
    return value


def _dataclass_fields(cls):
    return {field.name for field in fields(cls)}


def _array(value, where):
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a JSON array")
    return value


def _hex(value, where, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError(f"{where} must be a lowercase SHA-256 digest")


def _provenance(value, score_provenance, schema_version=SCHEMA_VERSION):
    _strict_object(value, _PROVENANCE_FIELDS, "provenance")
    kind = value["evidence_type"]
    if not isinstance(kind, str) or kind not in _LEGACY_EVIDENCE_TYPES | {
            _UNVERSIONED_EVIDENCE_TYPE, _PINNED_SCORER_EVIDENCE_TYPE}:
        raise ValueError("Unrecognized score evidence type")
    required_schema = _schema_for_kind(kind)
    if schema_version != required_schema:
        raise ValueError("Score evidence type does not match scored cache schema")
    for name in ("candidate_model_id", "candidate_model_revision",
                 "scorer_model_id", "scorer_model_revision"):
        if value[name] is not None and (not isinstance(value[name], str) or not value[name].strip()):
            raise ValueError("Model identifiers/revisions must be explicit text or null")
    if not isinstance(value["score_definition"], str) or not value["score_definition"].strip():
        raise ValueError("Scores require an explicit definition")
    if value["score_definition"] != score_provenance:
        raise ValueError("Cache score definition differs from decoder score provenance")
    _hex(value["config_sha256"], "config_sha256")
    _hex(value["prompt_sha256"], "prompt_sha256", nullable=True)
    if kind in {"authored_diagnostic", "migration_singleton_smoke"}:
        if value["scorer_model_id"] is not None or value["scorer_model_revision"] is not None:
            raise ValueError("Diagnostic/smoke scores must not claim a learned model scorer")
    elif kind == _UNVERSIONED_EVIDENCE_TYPE:
        if any(value[name] is None for name in ("candidate_model_id", "scorer_model_id", "prompt_sha256")):
            raise ValueError("Unversioned model scores require explicit candidate/scorer IDs and prompt hash")
        if any(value[name] is not None for name in ("candidate_model_revision", "scorer_model_revision")):
            raise ValueError("Unversioned model revisions must be null (unknown)")
    elif kind == _PINNED_SCORER_EVIDENCE_TYPE:
        if any(value[name] is None for name in (
                "candidate_model_id", "scorer_model_id", "prompt_sha256")):
            raise ValueError("Pinned scorer with unversioned candidates requires explicit model IDs and prompt hash")
        if value["candidate_model_revision"] is not None:
            raise ValueError("Unversioned candidate revision must remain null (unknown)")
        # This mixed provenance schema binds the actual scorer asset bytes,
        # rather than accepting a floating repository revision or a label.
        _hex(value["scorer_model_revision"], "Pinned scorer model asset revision")
    else:
        if any(value[name] is None for name in ("candidate_model_id", "candidate_model_revision",
                 "scorer_model_id", "scorer_model_revision", "prompt_sha256")):
            raise ValueError("Pinned model scores require explicit candidate/scorer revisions and prompt hash")
        if any(value[name] in {"main", "latest", "master", "unversioned", "unknown"}
               for name in ("candidate_model_revision", "scorer_model_revision")):
            raise ValueError("Floating revisions are not pinned model provenance")
    for prefix in ("candidate", "scorer"):
        if value[f"{prefix}_model_id"] is None and value[f"{prefix}_model_revision"] is not None:
            raise ValueError("A model revision requires its model identifier")


def _schema_for_kind(kind):
    if kind == _UNVERSIONED_EVIDENCE_TYPE:
        return UNVERSIONED_SCHEMA_VERSION
    if kind == _PINNED_SCORER_EVIDENCE_TYPE:
        return PINNED_SCORER_SCHEMA_VERSION
    return SCHEMA_VERSION


def _read_problem(data):
    _strict_object(data, _dataclass_fields(Problem), "problem")
    sources = []
    for row in _array(data["sources"], "sources"):
        _strict_object(row, _dataclass_fields(Source), "source")
        if any(not isinstance(value, str) for value in row.values()):
            raise ValueError("Source fields must be strings")
        sources.append(Source(**row))
    mentions = []
    for row in _array(data["mentions"], "mentions"):
        _strict_object(row, _dataclass_fields(Mention), "mention")
        readings = []
        for raw in _array(row["readings"], "readings"):
            _strict_object(raw, _dataclass_fields(Reading), "reading")
            item = dict(raw)
            if item["key"] is not None:
                item["key"] = tuple(_array(item["key"], "key"))
            for name in ("context_source_ids", "violations"):
                item[name] = tuple(_array(item[name], name))
            readings.append(Reading(**item))
        item = dict(row)
        item["readings"] = tuple(readings)
        item["context_source_ids"] = tuple(_array(item["context_source_ids"], "mention context"))
        mentions.append(Mention(**item))
    links = []
    for row in _array(data["links"], "links"):
        _strict_object(row, _dataclass_fields(Link), "link")
        item = dict(row)
        for name in ("context_source_ids", "violations"):
            item[name] = tuple(_array(item[name], name))
        if item["reference_span"] is not None:
            item["reference_span"] = tuple(_array(item["reference_span"], "reference_span"))
        links.append(Link(**item))
    penalties = []
    for row in _array(data["penalties"], "penalties"):
        _strict_object(row, _dataclass_fields(Penalty), "penalty")
        penalties.append(Penalty(**row))
    return Problem(data["cutoff"], tuple(sources), tuple(mentions), tuple(links),
                   data["score_provenance"],
                   tuple(_array(data["context_source_ids"], "problem context")),
                   tuple(penalties), data["beta"])


def _read_claim(row):
    _strict_object(row, _dataclass_fields(TemporalClaim), "claim")
    for name in ("start", "end"):
        _strict_object(row[name], _dataclass_fields(DayBounds), name)
    for span in _array(row["evidence_spans"], "claim evidence"):
        _strict_object(span, _dataclass_fields(EvidenceSpan), "claim evidence span")
        if not isinstance(span["quote"], str):
            raise ValueError("Evidence quotes must be strings")
    for name in ("state_observed_at", "context_source_ids"):
        _array(row[name], name)
    for name in ("scope", "raw_assertion_id", "derivation_note", "operation"):
        if not isinstance(row[name], str):
            raise ValueError(f"Claim {name} must be a string")
    # v0.3 source-only migration A used explicit null when no role qualifier was
    # extracted. Preserve that provenance value rather than invent an empty one.
    if row["role_qualifier"] is not None and not isinstance(row["role_qualifier"], str):
        raise ValueError("Claim role_qualifier must be a string or null")
    return claim_from_dict(row)


def _payload(problem, claims, aliases, provenance):
    # Keep every established evidence kind on its original byte representation.
    # New unversioned model outputs are explicit; they are never recast as
    # authored diagnostics or presented as pinned, reproducible model runs.
    schema_version = _schema_for_kind(provenance.get("evidence_type")) if isinstance(provenance, Mapping) else SCHEMA_VERSION
    return {
        "schema_version": schema_version,
        "problem": asdict(problem),
        "claims": [{"mention_id": key[0], "reading_id": key[1], "claim": claim_to_dict(claim)}
                   for key, claim in sorted(claims.items())],
        "aliases": _thaw(aliases),
        "provenance": _thaw(provenance),
        "source_sha256": {source.source_id: sha256(source.text.encode("utf-8")).hexdigest()
                          for source in problem.sources},
    }


def _validate_payload(data):
    _strict_object(data, {"schema_version", "problem", "claims", "aliases", "provenance",
                          "source_sha256"}, "cache payload")
    if data["schema_version"] not in (SCHEMA_VERSION, UNVERSIONED_SCHEMA_VERSION,
                                      PINNED_SCORER_SCHEMA_VERSION):
        raise ValueError("Unsupported scored cache schema")
    problem = _read_problem(data["problem"])
    _provenance(data["provenance"], problem.score_provenance, data["schema_version"])
    # Validation needs no enumeration and must not silently enforce one decoder's
    # experiment-specific search budget. Every decoder still applies its Limits.
    ceiling = math.prod(len(m.readings) for m in problem.mentions) * math.prod(
        1 + sum(link.mention_id == m.mention_id for link in problem.links)
        for m in problem.mentions)
    _Engine(problem, Limits(max_mentions=max(8, len(problem.mentions)),
                           max_readings_per_mention=max(5, max((len(m.readings) for m in problem.mentions), default=0)),
                           max_states=max(250_000, ceiling)))
    by_source = {source.source_id: source for source in problem.sources}
    _strict_object(data["source_sha256"], by_source, "source hashes")
    for sid, source in by_source.items():
        _hex(data["source_sha256"][sid], "source hash")
        if data["source_sha256"][sid] != sha256(source.text.encode("utf-8")).hexdigest():
            raise ValueError("Source text digest mismatch")
    claims = {}
    for row in _array(data["claims"], "claim bindings"):
        _strict_object(row, {"mention_id", "reading_id", "claim"}, "claim binding")
        if any(not isinstance(row[name], str) for name in ("mention_id", "reading_id")):
            raise ValueError("Binding IDs must be strings")
        key = row["mention_id"], row["reading_id"]
        if key in claims:
            raise ValueError("Duplicate reading-to-claim binding")
        claims[key] = _read_claim(row["claim"])
    if list(claims) != sorted(claims):
        raise ValueError("Claim bindings must use canonical mention/reading ID order")
    required = {(m.mention_id, r.reading_id) for m in problem.mentions for r in m.readings if not r.unresolved}
    if set(claims) != required:
        raise ValueError("Every resolved reading needs exactly one claim binding; unresolved readings need none")
    bounded = build_bounded_memory(problem.sources, claims.values(), problem.cutoff)
    if len(bounded.claims) != len(claims):
        raise ValueError("Claims must be built from the eligible source/context prefix")
    for mention in problem.mentions:
        for reading in mention.readings:
            if reading.unresolved:
                continue
            claim = claims[mention.mention_id, reading.reading_id]
            if (claim.source_id != mention.source_id or normalize_key(reading.key) != claim.key
                    or reading.value != claim.value):
                raise ValueError("Reading key/value/source differs from its temporal claim")
            if not any(span.start < mention.span_end and mention.span_start < span.end
                       for span in claim.evidence_spans):
                raise ValueError("Claim evidence must overlap its mention in the same source")
            exact_start = claim.start.lower if claim.start.lower == claim.start.upper else None
            exact_end = claim.end.lower if claim.end.lower == claim.end.upper else None
            observed = min(claim.state_observed_at) if claim.state_observed_at else None
            if (reading.effective_start, reading.effective_end, reading.observed_at) != (exact_start, exact_end, observed):
                raise ValueError("Reading exact dates differ from the minimal bounded-claim projection")
            declared_context = set(problem.context_source_ids) | set(mention.context_source_ids) | set(reading.context_source_ids)
            if not set(claim.context_source_ids) <= declared_context:
                raise ValueError("Claim context dependencies must also be declared in the candidate graph")
    aliases = _array(data["aliases"], "aliases")
    validate_aliases(aliases, claims.values(), problem.sources)
    return problem, claims, aliases, data["provenance"]


def make_cache(problem, claims, aliases, provenance):
    """Validate and defensively copy an inference-only shared-score artifact.

    No model call or scoring is performed. Caller-supplied diagnostic scores stay
    explicitly diagnostic, and all source/candidate/model configuration enters
    the digest. The copied nested maps/arrays cannot be modified in place.
    """
    if not isinstance(problem, Problem) or not isinstance(claims, Mapping):
        raise ValueError("make_cache expects a Problem and a reading-to-claim mapping")
    if any(not isinstance(key, tuple) or len(key) != 2 or
           any(not isinstance(part, str) for part in key) for key in claims):
        raise ValueError("Claim mapping keys must be (mention_id, reading_id) tuples")
    payload = _payload(problem, claims, aliases, provenance)
    # Round-trip through JSON provides one validation path and rejects NaN/Inf.
    payload = json.loads(_canonical(payload))
    checked_problem, checked_claims, checked_aliases, checked_provenance = _validate_payload(payload)
    digest = sha256(_canonical(payload)).hexdigest()
    return FrozenScoredInput(checked_problem, MappingProxyType(checked_claims),
                             _freeze(checked_aliases), _freeze(checked_provenance), digest)


def cache_digest(cache):
    """Recompute the complete cache digest, rejecting a forged dataclass wrapper."""
    checked = make_cache(cache.problem, cache.claims, cache.aliases, cache.provenance)
    if cache.digest != checked.digest:
        raise ValueError("Cache object digest mismatch")
    return checked.digest


def dump_cache(cache, path):
    """Write validated canonical JSON and return the payload digest."""
    digest = cache_digest(cache)
    payload = _payload(cache.problem, cache.claims, cache.aliases, cache.provenance)
    payload["digest"] = digest
    Path(path).write_bytes(_canonical(payload) + b"\n")
    return digest


def load_cache(path):
    """Read strict JSON: reject duplicates, nonfinite values and unknown fields."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=bad_constant)
    if not isinstance(data, dict) or "digest" not in data:
        raise ValueError("Scored cache requires a payload digest")
    expected = data.pop("digest")
    _hex(expected, "cache digest")
    actual = sha256(_canonical(data)).hexdigest()
    if expected != actual:
        raise ValueError("Scored cache digest mismatch")
    problem, claims, aliases, provenance = _validate_payload(data)
    return FrozenScoredInput(problem, MappingProxyType(claims), _freeze(aliases),
                             _freeze(provenance), actual)

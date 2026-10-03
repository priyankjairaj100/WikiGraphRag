"""Integrity checks for the natural-source developmental pilot.

``load_sources(manifest_path)`` loads exact UTF-8 representations and returns
Source records plus their separate provenance metadata. Its availability dates
are the manifest's *operational* conventions, never inferred from acceptance.
``load_prefix(input_path, loaded)`` checks an annotation input against those
representations and computes a digest over the input file's original bytes.
``validate_extraction(path_or_dict, prefix)`` checks source-only model output.

No function reads gold labels or converts extraction records into occupancy
Assertions. In particular, unknown starts, observations, negative assertions,
and planned states retain their different meanings for a downstream adapter.
Integrity checks establish provenance consistency, not semantic entailment or
historical public availability. This module performs no network requests.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Mapping

from .models import Source, validate_date


REPRESENTATION = "web_tool_line_text_joined_L0_through_last_line_UTF8_LF_no_other_normalization"
DEVELOPMENT = "development_only_not_benchmark"
BENCHMARK = "historical_benchmark_verified"
OPERATIONS = {"ASSERT", "RESTATES", "CHANGES", "CORRECTS", "UNRESOLVED"}
STATUSES = {"reported", "planned", "negative", "uncertain"}
RELATIONS = {"chief executive officer", "chief financial officer"}


@dataclass(frozen=True)
class LoadedSources:
    sources: tuple[Source, ...]
    metadata: dict[str, dict]
    manifest: dict
    purpose: str


@dataclass(frozen=True)
class PrefixInput:
    sources: tuple[Source, ...]
    metadata: dict[str, dict]
    prefixes: dict[str, str]
    input_sha256: str
    data: dict


def _fail(message):
    raise ValueError(message)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        _fail(f"{name} must be a nonempty string")
    return value


def _date(value, name, nullable=False):
    if value is None and nullable:
        return value
    _text(value, name)
    try:
        return validate_date(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid {name}: {value!r}") from exc


def _object(value, name):
    if not isinstance(value, dict):
        _fail(f"{name} must be an object")
    return value


def _list(value, name):
    if not isinstance(value, list):
        _fail(f"{name} must be a list")
    return value


def _hash(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail(f"{name} must be a lowercase SHA256 digest")
    return value


def _json_file(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    raw = Path(path).read_bytes()
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda value: _fail(f"Invalid JSON number: {value}"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid UTF-8 JSON file: {path}") from exc
    return _object(result, "JSON root"), raw


def _source_path(root, relative):
    _text(relative, "text_path")
    part = PurePosixPath(relative)
    if part.is_absolute() or ".." in part.parts or "\\" in relative:
        _fail(f"Unsafe source path: {relative}")
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        _fail(f"Source path escapes manifest directory or is not a file: {relative}")
    return resolved


def load_sources(manifest_path, *, purpose="development") -> LoadedSources:
    """Verify stored text; ``purpose='historical_benchmark'`` requires admission.

    The benchmark gate checks explicit manifest assertions, not their truth.
    Development manifests cannot be used for that purpose merely by opting in.
    Only this pilot's named representation is supported, preventing accidental
    substitution of HTML, normalized prose, or other extraction conventions.
    """
    if purpose not in {"development", "historical_benchmark"}:
        _fail(f"Unsupported source purpose: {purpose}")
    path = Path(manifest_path).resolve(strict=True)
    manifest, _ = _json_file(path)
    if manifest.get("status") not in {DEVELOPMENT, BENCHMARK}:
        _fail("Manifest has no recognized admission status")
    if purpose == "historical_benchmark" and manifest["status"] != BENCHMARK:
        _fail("Development-only sources are not admitted as a historical benchmark")
    sources, metadata, seen_paths = [], {}, set()
    for row in _list(manifest.get("sources"), "sources"):
        _object(row, "source")
        source_id = _text(row.get("source_id"), "source_id")
        if source_id in metadata:
            _fail(f"Duplicate source_id: {source_id}")
        _text(row.get("history_id"), "history_id")
        if row.get("representation") != REPRESENTATION:
            _fail(f"Unsupported representation for {source_id}")
        if row.get("admission_status") not in {DEVELOPMENT, BENCHMARK}:
            _fail(f"Unrecognized source admission status: {source_id}")
        if purpose == "historical_benchmark" and not (
                row.get("admission_status") == BENCHMARK
                and row.get("historical_bytes_verified") is True
                and row.get("first_public_availability_verified") is True):
            _fail(f"Source is not verified for historical benchmark use: {source_id}")
        source_path = _source_path(path.parent, row.get("text_path"))
        if source_path in seen_paths:
            _fail(f"Duplicate source representation path: {source_path.name}")
        seen_paths.add(source_path)
        raw = source_path.read_bytes()
        size = row.get("text_bytes")
        if type(size) is not int or size != len(raw):
            _fail(f"Text byte count mismatch for {source_id}")
        if hashlib.sha256(raw).hexdigest() != _hash(row.get("text_sha256"), "text_sha256"):
            _fail(f"Text SHA256 mismatch for {source_id}")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Source is not UTF-8: {source_id}") from exc
        _text(content, f"source text for {source_id}")
        if "\r" in content:
            _fail(f"Representation requires LF newlines: {source_id}")
        available = _date(row.get("operational_available_at"), "operational_available_at")
        basis = _text(row.get("availability_basis"), "availability_basis")
        _date(row.get("reported_publication_date"), "reported_publication_date", nullable=True)
        uri = _text(row.get("url"), "url")
        sources.append(Source(source_id, content, available, uri=uri,
                              availability_basis=basis,
                              provenance=f"natural_pilot:{row['admission_status']}"))
        metadata[source_id] = deepcopy(row)
    if not sources:
        _fail("Manifest must contain at least one source")
    return LoadedSources(tuple(sources), metadata, manifest, purpose)


def load_prefix(input_path, loaded: LoadedSources) -> PrefixInput:
    """Validate the complete eligible source subset for listed history cutoffs.

    Inline text, hashes, history IDs, operational dates and publication metadata
    must match the source manifest. Histories not requested by the input may be
    absent. Every eligible source of a requested history must be present.
    """
    data, raw = _json_file(input_path)
    prefixes = _object(data.get("prefixes"), "prefixes")
    if not prefixes:
        _fail("At least one history prefix is required")
    histories = {row["history_id"] for row in loaded.metadata.values()}
    for history_id, cutoff in prefixes.items():
        if history_id not in histories:
            _fail(f"Unknown prefix history: {history_id}")
        _date(cutoff, "prefix cutoff")
    by_id = {source.source_id: source for source in loaded.sources}
    selected, selected_metadata, seen = [], {}, set()
    for row in _list(data.get("sources"), "prefix sources"):
        _object(row, "prefix source")
        source_id = _text(row.get("source_id"), "source_id")
        if source_id in seen:
            _fail(f"Duplicate prefix source: {source_id}")
        seen.add(source_id)
        if source_id not in by_id:
            _fail(f"Unknown prefix source: {source_id}")
        metadata = loaded.metadata[source_id]
        history_id = row.get("history_id")
        if history_id != metadata["history_id"] or history_id not in prefixes:
            _fail(f"Prefix history mismatch: {source_id}")
        source = by_id[source_id]
        if source.available_at > prefixes[history_id]:
            _fail(f"Future source in history prefix: {source_id}")
        if row.get("text") != source.text:
            _fail(f"Prefix text differs from stored representation: {source_id}")
        for field in ("text_sha256", "operational_available_at", "availability_basis",
                      "reported_publication_date"):
            if row.get(field) != metadata.get(field):
                _fail(f"Prefix {field} mismatch: {source_id}")
        selected.append(source)
        selected_metadata[source_id] = deepcopy(metadata)
    eligible = {source.source_id for source in loaded.sources
                if loaded.metadata[source.source_id]["history_id"] in prefixes
                and source.available_at <= prefixes[loaded.metadata[source.source_id]["history_id"]]}
    if seen != eligible:
        _fail(f"Incomplete source prefix; missing sources: {sorted(eligible - seen)}")
    return PrefixInput(tuple(selected), selected_metadata, dict(prefixes),
                       hashlib.sha256(raw).hexdigest(), data)


def validate_extraction(path_or_dict, prefix: PrefixInput) -> dict:
    """Return a defensive copy after validating the prompt's extraction schema.

    Evidence uses exact Python Unicode codepoint offsets. Date fields may be
    null, but nonempty intervals must be positive. Targets must be existing
    same-key/same-history assertions and must not introduce a target cycle.
    Source/context membership is checked against each assertion's own history.
    This does not verify that quoted text entails a value, date or operation.
    """
    data = deepcopy(path_or_dict) if isinstance(path_or_dict, Mapping) else _json_file(path_or_dict)[0]
    _object(data, "extraction")
    _text(data.get("pass_id"), "pass_id")
    if _hash(data.get("input_sha256"), "input_sha256") != prefix.input_sha256:
        _fail("Extraction input_sha256 does not match exact prefix input bytes")
    backend = _object(data.get("backend"), "backend")
    if backend != {"model_revision": None, "decoding_settings": None,
                   "pinned": False, "kind": "conversational_model_agent"}:
        _fail("Backend metadata does not match this conversational extraction protocol")
    for field in ("disagreements_or_ambiguities", "exclusions", "limitations"):
        _list(data.get(field), field)
    by_id = {source.source_id: source for source in prefix.sources}
    rows = _list(data.get("assertions"), "assertions")
    assertions = {}
    required = {"assertion_id", "source_id", "subject", "relation", "scope", "value",
                "valid_from", "valid_to", "observed_on", "status", "qualifier",
                "operation", "target_id", "rationale", "evidence", "context_source_ids"}
    for row in rows:
        _object(row, "assertion")
        if set(row) != required:
            _fail(f"Assertion fields differ from protocol: {sorted(set(row) ^ required)}")
        assertion_id = _text(row["assertion_id"], "assertion_id")
        if assertion_id in assertions:
            _fail(f"Duplicate assertion_id: {assertion_id}")
        source_id = _text(row["source_id"], "source_id")
        if source_id not in by_id:
            _fail(f"Unknown or future assertion source: {source_id}")
        history = prefix.metadata[source_id]["history_id"]
        for field in ("subject", "scope", "value", "rationale"):
            _text(row[field], field)
        if row["relation"] not in RELATIONS:
            _fail(f"Unsupported relation: {row['relation']}")
        if row["status"] not in STATUSES or row["operation"] not in OPERATIONS:
            _fail("Unsupported assertion status or operation")
        if row["qualifier"] is not None and not isinstance(row["qualifier"], str):
            _fail("qualifier must be a string or null")
        for field in ("valid_from", "valid_to", "observed_on"):
            _date(row[field], field, nullable=True)
        if row["valid_from"] and row["valid_to"] and row["valid_from"] >= row["valid_to"]:
            _fail("Validity intervals must have positive duration")
        if row["observed_on"] and row["observed_on"] > prefix.prefixes[history]:
            _fail("Observed date falls after the supplied information cutoff")
        if row["target_id"] is not None:
            _text(row["target_id"], "target_id")
        if row["operation"] == "CORRECTS" and row["target_id"] is None:
            _fail("CORRECTS requires a target_id")
        context = _list(row["context_source_ids"], "context_source_ids")
        if len(context) != len(set(_text(item, "context source") for item in context)):
            _fail("Duplicate context source IDs")
        for context_id in context:
            if context_id not in by_id or prefix.metadata[context_id]["history_id"] != history:
                _fail(f"Context source is unavailable in this history prefix: {context_id}")
            if context_id == source_id:
                _fail("Context source must be another source, not the assertion's own source")
        evidence = _list(row["evidence"], "evidence")
        if not evidence:
            _fail(f"Assertion requires nonempty evidence: {assertion_id}")
        for span in evidence:
            _object(span, "evidence span")
            if set(span) != {"start", "end", "quote"}:
                _fail("Evidence must contain exactly start, end, quote")
            start, end = span["start"], span["end"]
            quote = _text(span["quote"], "evidence quote")
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(by_id[source_id].text):
                _fail(f"Invalid evidence offsets: {assertion_id}")
            if by_id[source_id].text[start:end] != quote:
                _fail(f"Evidence quote does not match codepoint offsets: {assertion_id}")
        assertions[assertion_id] = row
    for assertion_id, row in assertions.items():
        target_id = row["target_id"]
        if target_id is not None:
            if target_id not in assertions:
                _fail(f"Dangling assertion target: {target_id}")
            target = assertions[target_id]
            if prefix.metadata[row["source_id"]]["history_id"] != prefix.metadata[target["source_id"]]["history_id"]:
                _fail("Target belongs to another history")
            key = lambda record: tuple(record[field].strip().casefold() for field in ("subject", "relation", "scope"))
            if key(row) != key(target):
                _fail("Target belongs to another factual key")
            if row["operation"] == "CORRECTS" and by_id[row["source_id"]].available_at < by_id[target["source_id"]].available_at:
                _fail("Correction source precedes its target source")
        seen = set()
        current = assertion_id
        while current is not None:
            if current in seen:
                _fail("Assertion targets contain a cycle")
            seen.add(current)
            # All references are checked here too, including later list entries.
            if current not in assertions:
                _fail(f"Dangling assertion target: {current}")
            current = assertions[current]["target_id"]
    return data

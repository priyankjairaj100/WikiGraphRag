"""Immutable, question-free v0.5 wrapper for a v0.4 scored cache and policy.

The nested base cache has exactly the v0.4 serialization and digest. Policy
annotations, including asserted publisher authority, are separate evidence
inputs. Exact span checks and hashes do not certify their semantic truth.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType

from .bounded import EvidenceSpan
from .corrections import (CorrectionEvidence, CorrectionPolicy, SourceAuthority,
                          SupportDependency, validate_correction_policy)
from .scored_io import (FrozenScoredInput, _freeze, _payload as _base_payload,
                        _thaw, _validate_payload as _validate_base_payload,
                        cache_digest)


SCHEMA_VERSION = "0.5"
_PROVENANCE_FIELDS = {"evidence_type", "policy_builder_id", "policy_builder_revision",
                      "prompt_sha256", "config_sha256", "policy_description"}
_OUTER_FIELDS = {"schema_version", "base_cache", "base_digest", "correction_policy",
                 "correction_policy_digest", "correction_provenance"}


@dataclass(frozen=True)
class FrozenCorrectionInput:
    base: FrozenScoredInput
    policy: CorrectionPolicy
    provenance: Mapping
    digest: str


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _object(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"Unexpected or missing fields in {name}")
    return value


def _fields(cls):
    return {field.name for field in fields(cls)}


def _array(value, name):
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array")
    return value


def _text(value, name, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def _hex(value, name, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _span(data):
    _object(data, _fields(EvidenceSpan), "policy evidence span")
    if (type(data["start"]) is not int or type(data["end"]) is not int
            or not 0 <= data["start"] < data["end"]):
        raise ValueError("Policy evidence spans require nonempty integer offsets")
    _text(data["quote"], "policy evidence quote")
    return EvidenceSpan(**data)


def policy_to_dict(policy):
    """Serialize policy declarations only; perform no inference or annotation."""
    if not isinstance(policy, CorrectionPolicy):
        raise ValueError("Expected a CorrectionPolicy")
    return asdict(policy)


def _read_policy(data):
    _object(data, _fields(CorrectionPolicy), "correction policy")
    if data["version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported correction policy schema")
    authorities = []
    for item in _array(data["source_authorities"], "source authorities"):
        _object(item, _fields(SourceAuthority), "source authority")
        _text(item["source_id"], "authority source ID")
        _text(item["authority_id"], "authority ID")
        spans = tuple(_span(span) for span in _array(item["evidence_spans"], "authority evidence"))
        if not spans:
            raise ValueError("Source authority requires evidence spans")
        authorities.append(SourceAuthority(item["source_id"], item["authority_id"], spans))
    evidence = []
    for item in _array(data["correction_evidence"], "correction evidence"):
        _object(item, _fields(CorrectionEvidence), "correction evidence")
        _text(item["link_id"], "correction link ID")
        if item["action"] != "replace" or item["coverage"] != "whole_assertion":
            raise ValueError("Only explicit whole-assertion replacement evidence is supported")
        evidence.append(CorrectionEvidence(item["link_id"], _span(item["reference_span"]),
                                             item["action"], item["coverage"]))
    dependencies = []
    for item in _array(data["support_dependencies"], "support dependencies"):
        _object(item, _fields(SupportDependency), "support dependency")
        for name in ("dependent_claim_id", "prerequisite_claim_id"):
            _text(item[name], name)
        spans = tuple(_span(span) for span in _array(item["evidence_spans"], "dependency evidence"))
        if not spans:
            raise ValueError("Support dependency requires evidence spans")
        dependencies.append(SupportDependency(item["dependent_claim_id"],
                                               item["prerequisite_claim_id"], spans))
    return CorrectionPolicy(data["version"], tuple(authorities), tuple(evidence), tuple(dependencies))


def _default_provenance():
    description = ("Caller-supplied authored correction policy; source spans are "
                   "annotation assertions, not verified semantic entailment.")
    return {"evidence_type": "authored_diagnostic", "policy_builder_id": None,
            "policy_builder_revision": None, "prompt_sha256": None,
            "config_sha256": sha256(_canonical({"policy_version": SCHEMA_VERSION,
                                              "default_annotation_status": "authored_diagnostic"})).hexdigest(),
            "policy_description": description}


def _check_provenance(data):
    _object(data, _PROVENANCE_FIELDS, "correction provenance")
    kind = data["evidence_type"]
    if kind not in {"authored_diagnostic", "source_derived_annotation", "pinned_model_annotation"}:
        raise ValueError("Unrecognized correction evidence type")
    for name in ("policy_builder_id", "policy_builder_revision"):
        _text(data[name], name, nullable=True)
    _text(data["policy_description"], "policy_description")
    _hex(data["config_sha256"], "policy config_sha256")
    _hex(data["prompt_sha256"], "policy prompt_sha256", nullable=True)
    if data["policy_builder_id"] is None and data["policy_builder_revision"] is not None:
        raise ValueError("A builder revision requires its identifier")
    if kind == "source_derived_annotation" and any(
            data[name] is None for name in ("policy_builder_id", "prompt_sha256")):
        raise ValueError("Source-derived annotation requires explicit builder and prompt hash")
    if kind == "pinned_model_annotation":
        if any(data[name] is None for name in ("policy_builder_id", "policy_builder_revision", "prompt_sha256")):
            raise ValueError("Pinned model annotation requires model ID, revision and prompt hash")
        if data["policy_builder_revision"].strip().casefold() in {"main", "master", "latest", "unknown", "unversioned"}:
            raise ValueError("Floating policy model revisions are not pinned provenance")


def _payload(base, policy, provenance):
    nested = _base_payload(base.problem, base.claims, base.aliases, base.provenance)
    nested["digest"] = base.digest
    policy_data = policy_to_dict(policy)
    return {"schema_version": SCHEMA_VERSION, "base_cache": nested,
            "base_digest": base.digest, "correction_policy": policy_data,
            "correction_policy_digest": sha256(_canonical(policy_data)).hexdigest(),
            "correction_provenance": _thaw(provenance)}


def _validate_payload(data):
    _object(data, _OUTER_FIELDS, "correction cache payload")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported correction cache schema")
    nested = data["base_cache"]
    if not isinstance(nested, dict) or "digest" not in nested:
        raise ValueError("Nested v0.4 cache requires a payload digest")
    nested_payload = dict(nested)
    nested_digest = nested_payload.pop("digest")
    _hex(nested_digest, "nested base digest")
    _hex(data["base_digest"], "base lineage digest")
    if (sha256(_canonical(nested_payload)).hexdigest() != nested_digest
            or data["base_digest"] != nested_digest):
        raise ValueError("Nested base cache digest mismatch")
    problem, claims, aliases, provenance = _validate_base_payload(nested_payload)
    base = FrozenScoredInput(problem, MappingProxyType(claims), _freeze(aliases),
                              _freeze(provenance), nested_digest)
    _hex(data["correction_policy_digest"], "correction policy digest")
    if sha256(_canonical(data["correction_policy"])).hexdigest() != data["correction_policy_digest"]:
        raise ValueError("Correction policy digest mismatch")
    policy = _read_policy(data["correction_policy"])
    validate_correction_policy(base.problem, base.claims, policy)
    _check_provenance(data["correction_provenance"])
    return base, policy, _freeze(data["correction_provenance"])


def make_correction_cache(base, policy, correction_provenance=None):
    """Defensively copy one immutable shared-score base and source-only policy.

    Uncertified CORRECTS candidates may remain in the base graph. They become
    ineligible when selected, rather than being silently deleted or certified.
    Two-argument construction explicitly denotes an authored diagnostic.
    """
    if not isinstance(base, FrozenScoredInput) or not isinstance(policy, CorrectionPolicy):
        raise ValueError("Expected FrozenScoredInput and CorrectionPolicy")
    cache_digest(base)
    provenance = _default_provenance() if correction_provenance is None else correction_provenance
    payload = json.loads(_canonical(_payload(base, policy, provenance)))
    checked_base, checked_policy, checked_provenance = _validate_payload(payload)
    return FrozenCorrectionInput(checked_base, checked_policy, checked_provenance,
                                   sha256(_canonical(payload)).hexdigest())


def correction_cache_digest(cache):
    """Revalidate the whole immutable wrapper, including unaltered base lineage."""
    if not isinstance(cache, FrozenCorrectionInput):
        raise ValueError("Expected a FrozenCorrectionInput")
    checked = make_correction_cache(cache.base, cache.policy, cache.provenance)
    if cache.digest != checked.digest:
        raise ValueError("Correction cache object digest mismatch")
    return checked.digest


def dump_correction_cache(cache, path):
    """Write canonical v0.5 JSON with the unchanged v0.4 base cache embedded."""
    digest = correction_cache_digest(cache)
    payload = _payload(cache.base, cache.policy, cache.provenance)
    payload["digest"] = digest
    Path(path).write_bytes(_canonical(payload) + b"\n")
    return digest


def load_correction_cache(path):
    """Reject duplicate fields, nonfinite numbers, schema drift and stale hashes."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")

    data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=constant)
    if not isinstance(data, dict) or "digest" not in data:
        raise ValueError("Correction cache requires a payload digest")
    expected = data.pop("digest")
    _hex(expected, "correction cache digest")
    actual = sha256(_canonical(data)).hexdigest()
    if expected != actual:
        raise ValueError("Correction cache digest mismatch")
    base, policy, provenance = _validate_payload(data)
    return FrozenCorrectionInput(base, policy, provenance, actual)

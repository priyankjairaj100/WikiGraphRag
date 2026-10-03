"""Annotation-conditioned assertion-support lookup, not factual or temporal QA.

The unchanged correction materializer is the semantic authority. A supplied
memory is accepted only when it exactly reproduces an explicit input selection.
This checks consistency, not source authenticity, entailment, or extraction.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
from hashlib import sha256
import json
from typing import Literal

from .bounded import EvidenceSpan, TemporalClaim
from .corrections import CorrectionPolicy, materialize_corrected_selection
from .coupled import CoupledMemory
from .decoder import Link, Mention, Problem, Reading
from .models import Source


SupportStatus = Literal["active_support", "withdrawn_support", "unresolved"]


def _text(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


@dataclass(frozen=True)
class SupportQuery:
    """Lookup one exact candidate assertion ID; no ID normalization or routing."""

    assertion_id: str

    def __post_init__(self):
        _text(self.assertion_id, "assertion_id")


@dataclass(frozen=True)
class SupportReason:
    kind: Literal["selected_active", "explicit_replacement", "inactive_prerequisite",
                  "missing_prerequisite", "unknown_assertion", "assertion_not_selected"]
    related_assertion_id: str | None = None
    correction_link_id: str | None = None


@dataclass(frozen=True)
class SupportEvidence:
    """A provenance record; assertion records may themselves be withdrawn.

    source_identity_sha256 binds every field of the exact supplied Source,
    including text. IDs are hashes of this complete typed record, not inferred
    from human-readable assertion/source names.
    """

    role: Literal["assertion_record", "correction_reference", "essential_dependency",
                  "source_authority"]
    source_id: str
    source_identity_sha256: str
    assertion_ids: tuple[str, ...]
    evidence_spans: tuple[EvidenceSpan, ...]
    correction_link_id: str | None = None
    authority_id: str | None = None

    @property
    def evidence_id(self) -> str:
        return "support_evidence:" + _digest(asdict(self))


@dataclass(frozen=True)
class SupportAnswer:
    assertion_id: str
    status: SupportStatus
    selected_assertion: TemporalClaim | None
    reasons: tuple[SupportReason, ...]
    evidence: tuple[SupportEvidence, ...]
    context_source_ids: tuple[str, ...]
    information_cutoff: str

    @property
    def evidence_source_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_id for item in self.evidence}))


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


def _same_typed_value(left, right):
    """Unlike Python equality, do not accept True in place of 1 or tuple/list."""
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(_same_typed_value(getattr(left, item.name), getattr(right, item.name))
                   for item in fields(left))
    if isinstance(left, dict):
        return (len(left) == len(right)
                and all(type(key) is str for key in left)
                and set(left) == set(right)
                and all(_same_typed_value(left[key], right[key]) for key in left))
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _same_typed_value(a, b) for a, b in zip(left, right))
    return left == right


def _typed_inputs(query, memory, problem, reading_ids, link_ids, claims, policy):
    if type(query) is not SupportQuery:
        raise ValueError("query must be a SupportQuery")
    _text(query.assertion_id, "assertion_id")
    if type(memory) is not CoupledMemory or type(problem) is not Problem:
        raise ValueError("memory and problem must be typed core records")
    if type(policy) is not CorrectionPolicy:
        raise ValueError("policy must be a CorrectionPolicy")
    for name, values in (("reading_ids", reading_ids), ("link_ids", link_ids),
                         ("claims", claims)):
        if not isinstance(values, Mapping):
            raise ValueError(f"{name} must be a mapping")
    for mid, rid in reading_ids.items():
        _text(mid, "selected mention ID")
        _text(rid, "selected reading ID")
    for mid, lid in link_ids.items():
        _text(mid, "selected mention ID")
        if lid is not None:
            _text(lid, "selected link ID")
    for pair, claim in claims.items():
        if (type(pair) is not tuple or len(pair) != 2
                or any(type(value) is not str or not value.strip() for value in pair)
                or type(claim) is not TemporalClaim):
            raise ValueError("claims must map typed mention/reading pairs to TemporalClaim")
    for name, expected in (("sources", Source), ("mentions", Mention), ("links", Link)):
        values = getattr(problem, name)
        if type(values) is not tuple or any(type(item) is not expected for item in values):
            raise ValueError(f"Problem {name} must contain typed immutable records")
    for source in problem.sources:
        _text(source.source_id, "source_id")
        _text(source.text, "source text")
        for name in ("uri", "availability_basis", "provenance"):
            if type(getattr(source, name)) is not str:
                raise ValueError(f"Source {name} must be text")
    for mention in problem.mentions:
        _text(mention.mention_id, "mention_id")
        if type(mention.readings) is not tuple or any(
                type(item) is not Reading for item in mention.readings):
            raise ValueError("Mention readings must contain typed immutable records")
        for reading in mention.readings:
            _text(reading.reading_id, "reading_id")
    for link in problem.links:
        _text(link.link_id, "link_id")


def answer_support(query: SupportQuery, *, memory: CoupledMemory, problem: Problem,
                   reading_ids: Mapping[str, str], link_ids: Mapping[str, str | None],
                   claims: Mapping[tuple[str, str], TemporalClaim],
                   policy: CorrectionPolicy) -> SupportAnswer:
    """Look up support in a validated fixed selection, without inventing dates.

    active_support means a selected assertion survived this policy; it does not
    mean true, current, certain, unique, or entailed. withdrawn_support means the
    selected record lost support under the policy, not that its value is false.
    Known unselected and unknown IDs are unresolved. All inputs remain unchanged.
    """
    _typed_inputs(query, memory, problem, reading_ids, link_ids, claims, policy)
    canonical = materialize_corrected_selection(problem, reading_ids, link_ids, claims, policy)
    if not _same_typed_value(memory, canonical):
        raise ValueError("Supplied memory differs from re-materialized selection, source identity or ledger")
    by_id = {claim.claim_id: claim for claim in claims.values()}
    selected = {claim.claim_id: claim for (mid, rid), claim in claims.items()
                if reading_ids[mid] == rid}
    cid = query.assertion_id
    if cid not in selected:
        reason = "assertion_not_selected" if cid in by_id else "unknown_assertion"
        return SupportAnswer(cid, "unresolved", None, (SupportReason(reason),), (), (), canonical.cutoff)

    ledger = canonical.diagnostics["correction_ledger"]
    inactive = set(ledger["inactive_claim_ids"])
    notices = ledger["operative_notices"]
    reasons = []
    for notice in notices:
        if notice["target_claim_id"] == cid:
            reasons.append(SupportReason("explicit_replacement", notice["replacement_claim_id"],
                                         notice["link_id"]))
    for edge in policy.support_dependencies:
        if edge.dependent_claim_id == cid:
            prerequisite = edge.prerequisite_claim_id
            if prerequisite not in selected:
                reasons.append(SupportReason("missing_prerequisite", prerequisite))
            elif prerequisite in inactive:
                reasons.append(SupportReason("inactive_prerequisite", prerequisite))
    if cid not in inactive:
        reasons.append(SupportReason("selected_active"))

    # Include prerequisite ancestors and correction chains, not dependent
    # descendants or unrelated assertions sharing an entity/value/source.
    relevant = {cid}
    while True:
        before = set(relevant)
        for edge in policy.support_dependencies:
            if edge.dependent_claim_id in relevant and edge.dependent_claim_id in selected:
                relevant.add(edge.prerequisite_claim_id)
        for notice in notices:
            endpoints = {notice["target_claim_id"], notice["replacement_claim_id"]}
            if relevant & endpoints:
                relevant.update(endpoints)
        if relevant == before:
            break
    source_map = {source.source_id: source for source in canonical.sources}
    source_digests = {sid: _digest(asdict(source)) for sid, source in source_map.items()}
    evidence = []

    def add(role, sid, ids, spans, link_id=None, authority_id=None):
        evidence.append(SupportEvidence(role, sid, source_digests[sid], ids, spans,
                                        link_id, authority_id))

    for record_id in sorted(relevant & set(selected)):
        claim = selected[record_id]
        add("assertion_record", claim.source_id, (record_id,), claim.evidence_spans)
    certificates = {item.link_id: item for item in policy.correction_evidence}
    for notice in notices:
        if notice["target_claim_id"] in relevant:
            add("correction_reference", notice["notice_source_id"],
                (notice["replacement_claim_id"], notice["target_claim_id"]),
                (certificates[notice["link_id"]].reference_span,), notice["link_id"])
    for edge in policy.support_dependencies:
        if edge.dependent_claim_id in relevant and edge.dependent_claim_id in selected:
            add("essential_dependency", by_id[edge.dependent_claim_id].source_id,
                (edge.dependent_claim_id, edge.prerequisite_claim_id), edge.evidence_spans)
    evidence_sources = {item.source_id for item in evidence}
    for authority in policy.source_authorities:
        if authority.source_id in evidence_sources:
            add("source_authority", authority.source_id, (), authority.evidence_spans,
                authority_id=authority.authority_id)
    if cid in inactive:
        status = "withdrawn_support"
        context = ledger["inactive_claim_control_source_ids"].get(cid, [])
    else:
        status = "active_support"
        context = canonical.dependency_source_ids[cid]
    return SupportAnswer(cid, status, selected[cid],
                         tuple(sorted(reasons, key=lambda item: (item.kind,
                                     item.related_assertion_id or "", item.correction_link_id or ""))),
                         tuple(sorted(evidence, key=lambda item: item.evidence_id)),
                         tuple(sorted(context)), canonical.cutoff)

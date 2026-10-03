"""Evidence-only temporal bounds for selected, source-grounded claim readings.

This module deliberately does not choose candidate readings, learn scores, merge
identities, infer persistence, or implement correction chains.  It is a separate
materialization contract, not a replacement for the original memory baseline.
"""
from dataclasses import asdict, dataclass, field
from datetime import date
from math import inf
from typing import Iterable, Mapping

from .models import Prediction, Question, Source, validate_date


@dataclass(frozen=True)
class DayBounds:
    """Inclusive possible dates for one latent endpoint; None is unbounded."""

    lower: str | None = None
    upper: str | None = None

    def __post_init__(self):
        for value in (self.lower, self.upper):
            if value is not None:
                validate_date(value)
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Endpoint lower bound exceeds upper bound")


@dataclass(frozen=True)
class EvidenceSpan:
    """A nonempty exact [start,end) Python character slice of the source text."""

    start: int
    end: int
    quote: str

    def __post_init__(self):
        if type(self.start) is not int or type(self.end) is not int:
            raise ValueError("Evidence offsets must be integers")
        if self.start < 0 or self.end <= self.start or not self.quote:
            raise ValueError("Evidence spans must be nonempty, forward slices")
        if self.end - self.start != len(self.quote):
            raise ValueError("Evidence span length differs from quote length")


@dataclass(frozen=True)
class TemporalClaim:
    claim_id: str
    source_id: str
    subject: str
    relation: str
    value: str
    scope: str = ""
    polarity: str = "positive"
    modality: str = "reported_actual"
    reported_at: str | None = None
    start: DayBounds = field(default_factory=DayBounds)
    end: DayBounds = field(default_factory=DayBounds)
    state_observed_at: tuple[str, ...] = ()
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    context_source_ids: tuple[str, ...] = ()
    raw_assertion_id: str = ""
    derivation_note: str = ""
    role_qualifier: str = ""
    operation: str = "ASSERT"

    def __post_init__(self):
        for name in ("claim_id", "source_id", "subject", "relation", "value"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a nonempty string")
        if self.polarity not in {"positive", "negative"}:
            raise ValueError("Polarity must be positive or negative")
        if self.modality not in {"reported_actual", "announced_future", "conditional", "uncertain"}:
            raise ValueError("Unsupported modality")
        if self.operation == "CORRECTS":
            raise ValueError("CORRECTS requires correction-aware materialization; unsupported here")
        if self.operation not in {"ASSERT", "RESTATES", "CHANGES", "UNRESOLVED"}:
            raise ValueError("Unsupported operation")
        if self.reported_at is not None:
            validate_date(self.reported_at)
        for observed in self.state_observed_at:
            validate_date(observed)
            if self.modality == "reported_actual" and self.reported_at is not None and observed > self.reported_at:
                raise ValueError("An actual state observation cannot follow its report date")
        if not isinstance(self.start, DayBounds) or not isinstance(self.end, DayBounds):
            raise ValueError("start and end must be DayBounds")
        if not self.evidence_spans or any(not isinstance(span, EvidenceSpan) for span in self.evidence_spans):
            raise ValueError("Every temporal claim requires exact evidence spans")
        if any(not isinstance(sid, str) or not sid for sid in self.context_source_ids):
            raise ValueError("Context source IDs must be nonempty strings")
        if len(set(self.context_source_ids)) != len(self.context_source_ids):
            raise ValueError("Duplicate context source IDs")
        materialize_claim(self)

    @property
    def key(self) -> tuple[str, str, str]:
        return normalize_key((self.subject, self.relation, self.scope))


@dataclass(frozen=True)
class Membership:
    may: bool
    must: bool


@dataclass(frozen=True)
class IntervalEnvelope:
    """Propagated integer ordinal bounds; None denotes an unbounded side.

    Ordinals immediately outside Python's date range can arise from the strict
    s < e constraint. They are internal boundaries, not invented source dates.
    """

    start_lower: int | None
    start_upper: int | None
    end_lower: int | None
    end_upper: int | None

    def membership(self, event_time: str) -> Membership:
        t = date.fromisoformat(validate_date(event_time)).toordinal()
        sl = -inf if self.start_lower is None else self.start_lower
        su = inf if self.start_upper is None else self.start_upper
        el = -inf if self.end_lower is None else self.end_lower
        eu = inf if self.end_upper is None else self.end_upper
        return Membership(may=sl <= t < eu, must=su <= t < el)


def _ordinal(value: str | None, fallback: float) -> int | float:
    return fallback if value is None else date.fromisoformat(value).toordinal()


def _finite(value: int | float) -> int | None:
    return None if value in {-inf, inf} else int(value)


def materialize_claim(claim: TemporalClaim) -> IntervalEnvelope:
    """Propagate endpoint and observation constraints, rejecting empty models.

    For integer-day endpoints: s <= every observation < e, and s + 1 <= e.
    These difference constraints have a closed-form bound propagation here.
    """
    sl = _ordinal(claim.start.lower, -inf)
    su = _ordinal(claim.start.upper, inf)
    el = _ordinal(claim.end.lower, -inf)
    eu = _ordinal(claim.end.upper, inf)
    if claim.state_observed_at:
        observed = [date.fromisoformat(t).toordinal() for t in claim.state_observed_at]
        su = min(su, min(observed))
        el = max(el, max(observed) + 1)
    su = min(su, eu - 1)
    el = max(el, sl + 1)
    if sl > su or el > eu:
        raise ValueError("No positive-duration interval satisfies endpoint/observation bounds")
    return IntervalEnvelope(_finite(sl), _finite(su), _finite(el), _finite(eu))


def claim_membership(claim: TemporalClaim, event_time: str) -> Membership:
    """Temporal membership only; polarity and modality are query-layer concerns."""
    return materialize_claim(claim).membership(event_time)


def normalize_key(key: tuple[str, str, str]) -> tuple[str, str, str]:
    if len(key) != 3 or any(not isinstance(part, str) for part in key):
        raise ValueError("A predicted key must contain subject, relation, and scope strings")
    return tuple(part.strip().casefold() for part in key)


def claim_from_dict(record: Mapping) -> TemporalClaim:
    """Strict dataclass schema; unknown fields raise rather than disappear."""
    fields = dict(record)
    fields["start"] = DayBounds(**fields.get("start", {}))
    fields["end"] = DayBounds(**fields.get("end", {}))
    fields["evidence_spans"] = tuple(EvidenceSpan(**span) for span in fields.get("evidence_spans", ()))
    for name in ("state_observed_at", "context_source_ids"):
        fields[name] = tuple(fields.get(name, ()))
    return TemporalClaim(**fields)


def claim_to_dict(claim: TemporalClaim) -> dict:
    record = asdict(claim)
    for name in ("state_observed_at", "context_source_ids", "evidence_spans"):
        record[name] = list(record[name])
    return record


@dataclass(frozen=True)
class BoundedMemory:
    sources: tuple[Source, ...]
    claims: tuple[TemporalClaim, ...]
    cutoff: str
    diagnostics: dict = field(default_factory=dict)

    def answer_key(self, question: Question, key: tuple[str, str, str], query_mode: str = "announced_schedule") -> Prediction:
        """Answer a predicted factual key; routing remains a separate component.

        Each key is assumed single-valued after scope qualification. This
        method does not infer coreference or treat unknown intervals as empty.
        """
        if question.information_cutoff != self.cutoff:
            raise ValueError("Question cutoff must equal the materialized source prefix")
        if query_mode not in {"reported_actual", "announced_schedule"}:
            raise ValueError("Unknown query mode")
        key = normalize_key(key)
        relevant = []
        excluded_plans = []
        excluded_projections = []
        actual_horizons = {}
        source_map = {source.source_id: source for source in self.sources}
        for claim in self.claims:
            if claim.key != key:
                continue
            if claim.modality == "announced_future" and query_mode == "reported_actual":
                excluded_plans.append(claim.claim_id)
                continue
            if query_mode == "reported_actual" and claim.modality == "reported_actual":
                horizon = claim.reported_at or source_map[claim.source_id].available_at
                basis = "reported_at" if claim.reported_at is not None else "source_available_at_fallback"
                actual_horizons[claim.claim_id] = {"date": horizon, "basis": basis}
                if question.event_time > horizon:
                    excluded_projections.append(claim.claim_id)
                    continue
            membership = claim_membership(claim, question.event_time)
            if membership.may:
                certain = membership.must and claim.modality not in {"conditional", "uncertain"}
                relevant.append((claim, certain))
        positives = [(claim, certain) for claim, certain in relevant if claim.polarity == "positive"]
        negatives = [(claim, certain) for claim, certain in relevant if claim.polarity == "negative"]
        canonical = lambda value: " ".join(value.casefold().split())
        pos_by_value: dict[str, list] = {}
        neg_by_value: dict[str, list] = {}
        for claim, certain in positives:
            pos_by_value.setdefault(canonical(claim.value), []).append((claim, certain))
        for claim, certain in negatives:
            neg_by_value.setdefault(canonical(claim.value), []).append((claim, certain))
        certain_values = {value for value, claims in pos_by_value.items() if any(certain for _, certain in claims)}
        blocked_values = {value for value in pos_by_value if value not in certain_values and any(certain for _, certain in neg_by_value.get(value, []))}
        possible_values = set(pos_by_value) - blocked_values
        conflicts = {value for value in certain_values if value in neg_by_value}
        diagnostics = dict(self.diagnostics)
        diagnostics.update({
            "materializer": "bounded_evidence_only_v0.3",
            "query_mode": query_mode,
            "predicted_key": list(key),
            "persistence_assumption": "connected_interval_within_each_selected_episode; no_forward_persistence",
            "cross_claim_constraint_propagation": "none; selected_intervals_checked_independently",
            "single_valued_after_scope": True,
            "possible_value_keys": sorted(possible_values),
            "certain_value_keys": sorted(certain_values),
            "blocked_possible_value_keys": sorted(blocked_values),
            "positive_negative_conflicts": sorted(conflicts),
            "excluded_plan_claim_ids": sorted(excluded_plans),
            "excluded_actual_projection_claim_ids": sorted(excluded_projections),
            "actual_horizons": actual_horizons,
            "planned_claim_ids": sorted(claim.claim_id for claim, _ in relevant if claim.modality == "announced_future"),
        })
        if len(certain_values) == 1 and possible_values == certain_values and not conflicts:
            value = next(iter(certain_values))
            supporters = [claim for claim, certain in pos_by_value[value] if certain]
            # Merely possible later observations are intentionally not cited as
            # support for a definite earlier state.
            status = "answered"
            values = (min(supporters, key=lambda claim: claim.claim_id).value,)
            evidence = supporters + [claim for claim, certain in negatives if certain and canonical(claim.value) in blocked_values]
        elif possible_values:
            status = "indeterminate"
            values = ()
            evidence = [claim for claim, _ in positives if canonical(claim.value) in possible_values]
            evidence += [claim for claim, _ in negatives if canonical(claim.value) in possible_values]
        else:
            status = "abstain"
            values = ()
            evidence = []
        diagnostics["supporting_claim_modalities"] = {claim.claim_id: claim.modality for claim in evidence}
        primary_sources = {claim.source_id for claim in evidence}
        context_sources = {sid for claim in evidence for sid in claim.context_source_ids}
        diagnostics["primary_evidence_source_ids"] = sorted(primary_sources)
        diagnostics["context_dependency_source_ids"] = sorted(context_sources)
        return Prediction(
            question.question_id, status, values,
            tuple(sorted(primary_sources | context_sources)),
            tuple(sorted({claim.claim_id for claim in evidence})), diagnostics,
        )


def build_bounded_memory(sources: Iterable[Source], claims: Iterable[TemporalClaim], cutoff: str) -> BoundedMemory:
    """Validate complete input, then admit only source/context-eligible claims.

    This function accepts already selected claim readings. All dependency IDs
    must resolve, including those in future records. It cannot detect hidden
    model context that an extractor failed to declare.
    """
    validate_date(cutoff)
    sources = tuple(sources)
    claims = tuple(claims)
    source_map = {source.source_id: source for source in sources}
    if len(source_map) != len(sources):
        raise ValueError("Duplicate source ID")
    if len({claim.claim_id for claim in claims}) != len(claims):
        raise ValueError("Duplicate claim ID")
    for claim in claims:
        dependencies = (claim.source_id, *claim.context_source_ids)
        if any(sid not in source_map for sid in dependencies):
            raise ValueError(f"Dangling source/context reference in {claim.claim_id}")
        source = source_map[claim.source_id]
        if claim.reported_at is not None and claim.reported_at > source.available_at:
            raise ValueError("Claim report date cannot follow its source availability date")
        if claim.modality == "reported_actual" and claim.reported_at is None and any(observed > source.available_at for observed in claim.state_observed_at):
            raise ValueError("An actual state observation cannot follow source availability when report date is absent")
        text = source.text
        for span in claim.evidence_spans:
            if span.end > len(text) or text[span.start:span.end] != span.quote:
                raise ValueError(f"Nonmatching evidence span in {claim.claim_id}")
    eligible_sources = tuple(source for source in sources if source.available_at <= cutoff)
    eligible_ids = {source.source_id for source in eligible_sources}
    eligible_claims = tuple(claim for claim in claims if claim.source_id in eligible_ids and all(sid in eligible_ids for sid in claim.context_source_ids))
    excluded = [claim.claim_id for claim in claims if claim not in eligible_claims]
    return BoundedMemory(eligible_sources, eligible_claims, cutoff, {
        "excluded_future_source_or_context_claim_ids": sorted(excluded),
        "source_prefix_size": len(eligible_sources),
        "eligible_claim_count": len(eligible_claims),
    })

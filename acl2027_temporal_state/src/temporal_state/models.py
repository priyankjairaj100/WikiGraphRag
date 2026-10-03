"""Inference-only records. Evaluation labels belong in a separate module.

Version 0.1 uses ISO calendar dates at day resolution. Availability is an
explicit corpus cutoff, not a claim that publication metadata proves a version
was publicly accessible then. Assertions are predictions, never gold labels.
"""
from dataclasses import dataclass, field
from datetime import date


def validate_date(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"Expected canonical ISO calendar date: {value!r}")
    return value


@dataclass(frozen=True)
class Source:
    source_id: str
    text: str
    available_at: str
    uri: str = ""
    availability_basis: str = "synthetic_fixture"
    provenance: str = "synthetic"

    def __post_init__(self):
        validate_date(self.available_at)


@dataclass(frozen=True)
class Assertion:
    assertion_id: str
    source_id: str
    subject: str
    relation: str
    value: str
    valid_from: str | None
    valid_to: str | None = None
    scope: str = ""
    operation: str = "ASSERT"
    target_id: str | None = None
    support_quote: str = ""
    predicted_by: str = "hand_authored_diagnostic"
    context_source_ids: tuple[str, ...] = ()

    def __post_init__(self):
        for value in (self.valid_from, self.valid_to):
            if value is not None:
                validate_date(value)
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("Validity intervals must have positive duration")
        if self.operation not in {"ASSERT", "RESTATES", "CHANGES", "CORRECTS", "UNRESOLVED"}:
            raise ValueError(f"Unsupported operation: {self.operation}")
        if self.operation == "CORRECTS" and self.target_id is None:
            raise ValueError("A correction requires an explicit target")

    @property
    def key(self) -> tuple[str, str, str]:
        return tuple(x.strip().casefold() for x in (self.subject, self.relation, self.scope))


@dataclass(frozen=True)
class Question:
    question_id: str
    text: str
    event_time: str
    information_cutoff: str

    def __post_init__(self):
        validate_date(self.event_time)
        validate_date(self.information_cutoff)


@dataclass(frozen=True)
class Episode:
    key: tuple[str, str, str]
    value: str
    start: str
    end: str | None
    assertion_ids: tuple[str, ...]
    source_ids: tuple[str, ...]

    def contains(self, event_time: str) -> bool:
        return self.start <= event_time and (self.end is None or event_time < self.end)


@dataclass(frozen=True)
class Prediction:
    question_id: str
    status: str
    values: tuple[str, ...] = ()
    evidence_source_ids: tuple[str, ...] = ()
    evidence_assertion_ids: tuple[str, ...] = ()
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in {"answered", "indeterminate", "abstain"}:
            raise ValueError(f"Unsupported prediction status: {self.status}")

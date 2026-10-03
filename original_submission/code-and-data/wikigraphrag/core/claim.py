"""The claim tuple ``c = (s, r, v, t)`` and timestamp parsing.

A claim is the atomic unit of the memory. The *detector* works from ``text`` and
``timestamp`` alone (Section 3, "estimates the relation ... from text alone"); the parsed
``subject`` / ``relation`` / ``value`` fields are gold annotations used only to *build*
benchmarks and score detection. Keeping the two separate is what lets us measure the
detector honestly instead of letting it peek at gold keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_YEAR = re.compile(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b")  # 1500..2199, a plausible CE year


def parse_timestamp(value: Any) -> Optional[float]:
    """Normalise a date/year into a single sortable float (fractional year).

    Accepts ``datetime``/``date``, an ``int``/``float`` year, an ISO ``YYYY-MM-DD`` string,
    or free text containing a 4-digit year. Returns ``None`` when nothing parses, so callers
    can decide how to treat undated claims (the detector requires an order, Eq. 1).
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        d = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
        # fractional year keeps day-level ordering without a full calendar
        year_start = datetime(d.year, 1, 1)
        next_year = datetime(d.year + 1, 1, 1)
        return d.year + (d - year_start).total_seconds() / (next_year - year_start).total_seconds()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = _ISO_DATE.search(value)
        if m:
            return parse_timestamp(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        m = _YEAR.search(value)
        if m:
            return float(m.group(1))
    return None


@dataclass(frozen=True)
class Claim:
    """A single time-stamped source or benchmark-rendered claim span.

    Attributes
    ----------
    cid:
        Stable unique id (used as the graph node key and in the claim-to-file map).
    text:
        Detector-visible text. Genuine-prose datasets retain source extracts; structured
        benchmarks use documented renderings of their records.
    timestamp:
        Sortable source time ``t`` (fractional year). ``None`` means undated; such claims
        can never be the *later* member of a supersession pair.
    doc_id:
        Provenance / page node id.
    subject, relation, value:
        Gold annotations (``None`` for raw prose). Evaluation only -- never read by the
        detector's text-driven path.
    valid_time:
        Optional stated validity time, distinct from the document time. Reserved for the
        validity-interval extension (PLAN section 5b-A); ``timestamp`` is used for the
        faithful reproduction.
    span:
        ``(start, end)`` char offsets within the source document, when known.
    """

    cid: str
    text: str
    timestamp: Optional[float]
    doc_id: str = "doc"
    subject: Optional[str] = None
    relation: Optional[str] = None
    value: Optional[str] = None
    valid_time: Optional[float] = None
    span: Optional[Tuple[int, int]] = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def gold_key(self) -> Optional[Tuple[str, str]]:
        """The gold subject-relation key ``kappa(c)``, or ``None`` if unannotated."""
        if self.subject is None or self.relation is None:
            return None
        return (self.subject.strip().lower(), self.relation.strip().lower())

    @property
    def t(self) -> Optional[float]:
        """Alias for ``timestamp`` matching the paper's notation ``t_c``."""
        return self.timestamp


def key(claim: Claim) -> Optional[Tuple[str, str]]:
    """Gold key ``kappa(c) = (s_c, r_c)`` used to define the superseded set (Eq. 1)."""
    return claim.gold_key

"""Diagnostic temporal memory over *predicted*, day-resolution assertions.

This baseline deliberately does not extract facts or infer aliases. Missing ends
use a functional-state persistence assumption: the first strictly later,
different value ends an open assertion. Overlapping explicit same-value end
bounds also limit inferred persistence; this inheritance is reported. Explicit
intervals themselves are never overwritten.
Observation dates must not be passed as effective dates without that assumption.
"""
from collections import defaultdict
from dataclasses import dataclass, field
import re

from .models import Assertion, Episode, Prediction, Question, Source, validate_date


def _norm(value):
    return " ".join(value.casefold().split())


def _tokens(text):
    # A lexical abbreviation expansion, not a learned semantic query resolver.
    text = re.sub(r"\bceo\b", "chief executive officer", text.casefold())
    return set(re.findall(r"[^\W_]+", text)) - {"the", "a", "an", "of", "is", "was"}


@dataclass
class Memory:
    sources: tuple[Source, ...]
    assertions: tuple[Assertion, ...]
    cutoff: str
    episodes: list[Episode]
    diagnostics: dict = field(default_factory=dict)
    _active: tuple[Assertion, ...] = field(default=(), repr=False)
    _uncertain: tuple[Assertion, ...] = field(default=(), repr=False)

    def _route(self, text):
        query_tokens = _tokens(text)
        candidates = []
        for key in sorted({a.key for a in self.assertions}):
            subject, relation, scope = (_tokens(x) for x in key)
            if not subject or not relation:
                continue
            if subject <= query_tokens and relation <= query_tokens:
                if scope and not scope <= query_tokens:
                    continue
                candidates.append(((len(subject), len(relation), len(scope)), key))
        if not candidates:
            return []
        best = max(score for score, _ in candidates)
        return [key for score, key in candidates if score == best]

    def answer(self, question: Question, policy="episodes") -> Prediction:
        if question.information_cutoff != self.cutoff:
            raise ValueError("Question cutoff differs from memory cutoff; rebuild for that cutoff")
        legacy_policy = policy if policy == "newest_publication" else None
        if legacy_policy:
            policy = "newest_available"
        if policy not in {"episodes", "newest_available"}:
            raise ValueError(f"Unknown retrieval policy: {policy}")
        keys = self._route(question.text)
        diagnostic = {"policy": policy, "routing": "lexical_predicted_key_labels"}
        if legacy_policy:
            diagnostic["legacy_policy_alias"] = legacy_policy
        if len(keys) != 1:
            diagnostic.update(reason="no_lexical_key" if not keys else "ambiguous_lexical_key",
                              candidate_keys=keys)
            return Prediction(question.question_id, "abstain" if not keys else "indeterminate",
                              diagnostics=diagnostic)
        key = keys[0]
        diagnostic["routed_key"] = key
        if policy == "newest_available":
            source_times = {s.source_id: s.available_at for s in self.sources}
            records = [a for a in self.assertions if a.key == key]
            latest = max(source_times[a.source_id] for a in records)
            records = [a for a in records if source_times[a.source_id] == latest]
            values = {_norm(a.value): a.value for a in records if a.value.strip()}
            uncertain = any(a.operation == "UNRESOLVED" for a in records)
            diagnostic.update(reason="latest_available_source", selected_source_date=latest,
                              ignores_effective_time=True)
            return _prediction(question, records, values, uncertain, diagnostic)

        hits = [e for e in self.episodes if e.key == key and e.contains(question.event_time)]
        uncertain = [a for a in self._uncertain if a.key == key
                     and (a.valid_from is None or a.valid_from <= question.event_time)
                     and (a.valid_to is None or question.event_time < a.valid_to)]
        values = {_norm(e.value): e.value for e in hits}
        source_ids = {sid for e in hits for sid in e.source_ids}
        assertion_ids = {aid for e in hits for aid in e.assertion_ids}
        source_ids.update(a.source_id for a in uncertain)
        assertion_ids.update(a.assertion_id for a in uncertain)
        # Contradictory corrections cannot be arbitrated merely by recency.
        branch_ids = self.diagnostics["unresolved_correction_assertion_ids"]
        branch_records = [a for a in self.assertions if a.key == key and a.assertion_id in branch_ids]
        uncertain.extend(branch_records)
        source_ids.update(a.source_id for a in branch_records)
        assertion_ids.update(a.assertion_id for a in branch_records)
        if uncertain or len(values) > 1:
            diagnostic.update(reason="unresolved_temporal_state",
                              competing_values=sorted(set(values.values()) |
                                  {a.value for a in uncertain if a.value.strip()}))
            status, answer_values = "indeterminate", ()
        elif values:
            diagnostic["reason"] = "unique_supported_episode"
            status, answer_values = "answered", tuple(values.values())
        else:
            diagnostic["reason"] = "no_supported_episode_at_event_time"
            status, answer_values = "abstain", ()
        return Prediction(question.question_id, status, answer_values,
                          tuple(sorted(source_ids)), tuple(sorted(assertion_ids)), diagnostic)


def _prediction(question, records, values, uncertain, diagnostic):
    if uncertain or len(values) != 1:
        diagnostic["competing_values"] = sorted(values.values())
        status, answer_values = "indeterminate", ()
    else:
        status, answer_values = "answered", tuple(values.values())
    return Prediction(question.question_id, status, answer_values,
                      tuple(sorted({a.source_id for a in records})),
                      tuple(sorted({a.assertion_id for a in records})), diagnostic)


def _unique(records, attr, kind):
    out = {}
    for record in records:
        identifier = getattr(record, attr)
        if not identifier or not identifier.strip():
            raise ValueError(f"Empty {kind} identifier")
        if identifier in out:
            raise ValueError(f"Duplicate {kind} identifier: {identifier}")
        out[identifier] = record
    return out


def build_memory(sources, assertions, cutoff) -> Memory:
    """Validate the complete input, then build an availability-safe snapshot.

    A future source, or any future source consulted while extracting an
    assertion, makes that assertion unavailable. Complete-input validation
    happens before filtering: even malformed future input raises an error;
    its semantic contents are never used for snapshot inference. Blank support
    quotes are permitted for diagnostic extraction, but nonempty quotes must
    be exact source substrings. Corrections target exact
    assertion IDs at the same key; explicit chains can revise earlier
    corrections. Sibling corrections are conservatively unresolved.
    """
    validate_date(cutoff)
    source_by_id = _unique(sources, "source_id", "source")
    assertion_by_id = _unique(assertions, "assertion_id", "assertion")
    for a in assertion_by_id.values():
        if not a.subject.strip() or not a.relation.strip():
            raise ValueError(f"Assertion {a.assertion_id} has an empty subject or relation")
        if not a.value.strip() and a.operation != "UNRESOLVED":
            raise ValueError(f"Assertion {a.assertion_id} has an empty value")
        for sid in (a.source_id, *a.context_source_ids):
            if sid not in source_by_id:
                raise ValueError(f"Assertion {a.assertion_id} has dangling source reference: {sid}")
        if a.support_quote and a.support_quote not in source_by_id[a.source_id].text:
            raise ValueError(f"Assertion {a.assertion_id} support quote is not an exact source span")
        if a.target_id is not None:
            if a.target_id not in assertion_by_id:
                raise ValueError(f"Assertion {a.assertion_id} has dangling target: {a.target_id}")
            if a.target_id == a.assertion_id:
                raise ValueError("An assertion cannot target itself")
            if a.key != assertion_by_id[a.target_id].key:
                raise ValueError("Cross-key targets require explicit entity resolution; unsupported")
            if a.operation == "CORRECTS":
                target = assertion_by_id[a.target_id]
                if target.source_id in source_by_id and (
                        source_by_id[a.source_id].available_at < source_by_id[target.source_id].available_at):
                    raise ValueError("A correction cannot predate its target source's availability")
    # Validate target-reference cycles before filtering (also catches hidden bad input).
    completed = set()
    for start in assertion_by_id:
        path, current = set(), start
        while current is not None and current not in completed:
            if current in path:
                raise ValueError("Cyclic assertion target references")
            path.add(current)
            current = assertion_by_id[current].target_id
        completed.update(path)

    available = {sid for sid, source in source_by_id.items() if source.available_at <= cutoff}
    future_source = sorted(set(source_by_id) - available)
    omitted_source = sorted(a.assertion_id for a in assertion_by_id.values() if a.source_id not in available)
    omitted_context = sorted(a.assertion_id for a in assertion_by_id.values()
                             if any(sid not in available for sid in a.context_source_ids))
    eligible = {aid: a for aid, a in assertion_by_id.items()
                if aid not in set(omitted_source) | set(omitted_context)}
    omitted_target = []
    while True:
        remove = [aid for aid, a in eligible.items()
                  if a.target_id is not None and a.target_id not in eligible]
        if not remove:
            break
        for aid in remove:
            omitted_target.append(aid)
            del eligible[aid]
    corrections = defaultdict(list)
    for a in eligible.values():
        if a.operation == "CORRECTS":
            corrections[a.target_id].append(a.assertion_id)
    replaced = set(corrections)
    branch_ids = {aid for target, ids in corrections.items() if len(ids) > 1
                  for aid in [target, *ids]}
    active = [a for aid, a in eligible.items() if aid not in replaced]
    uncertain = [a for a in active if a.valid_from is None or a.operation == "UNRESOLVED"]
    dated = [a for a in active if a.valid_from is not None and a.operation != "UNRESOLVED"]
    diagnostics = {
        "omitted_future_source_ids": future_source,
        "omitted_future_assertion_ids": omitted_source,
        "omitted_future_context_assertion_ids": omitted_context,
        "omitted_unavailable_target_assertion_ids": sorted(omitted_target),
        "replaced_assertion_ids": sorted(replaced),
        "correction_lineage": {aid: eligible[aid].target_id for ids in corrections.values() for aid in ids},
        "unresolved_correction_assertion_ids": sorted(branch_ids),
        "uncertain_assertion_ids": sorted(a.assertion_id for a in uncertain),
        "blank_support_quote_assertion_ids": sorted(a.assertion_id for a in eligible.values()
                                                     if not a.support_quote),
        "same_day_correction_assertion_ids": sorted(a.assertion_id for a in eligible.values()
            if a.operation == "CORRECTS" and source_by_id[a.source_id].available_at ==
               source_by_id[eligible[a.target_id].source_id].available_at),
        "inherited_explicit_end_by_assertion": {},
        "extraction_regime": "provided_predictions_not_document_extraction",
        "input_validation": "complete_input_before_cutoff_filtering",
        "open_end_assumption": "persists_until_next_different_start_or_overlapping_explicit_same_value_end",
    }
    episodes = _episodes(dated, diagnostics)
    return Memory(tuple(source_by_id[sid] for sid in sorted(available)),
                  tuple(eligible[aid] for aid in sorted(eligible)), cutoff, episodes,
                  diagnostics, tuple(active), tuple(uncertain))


def _episodes(assertions, diagnostics):
    groups = defaultdict(list)
    for a in assertions:
        groups[a.key].append(a)
    output = []
    for key, group in sorted(groups.items()):
        group.sort(key=lambda a: (a.valid_from, a.assertion_id))
        value_intervals = defaultdict(list)
        for a in group:
            end = a.valid_to
            if end is None:
                end = next((b.valid_from for b in group
                            if b.valid_from > a.valid_from and _norm(b.value) != _norm(a.value)), None)
                explicit_ends = [b.valid_to for b in group
                                 if b.valid_to is not None and a.valid_from < b.valid_to
                                 and (end is None or b.valid_from < end)
                                 and _norm(b.value) == _norm(a.value)]
                if explicit_ends:
                    bounded = min([*explicit_ends, *([end] if end else [])])
                    if bounded != end:
                        diagnostics["inherited_explicit_end_by_assertion"][a.assertion_id] = bounded
                    end = bounded
            value_intervals[_norm(a.value)].append((a.valid_from, end, a))
        for value, intervals in value_intervals.items():
            runs = []
            for start, end, a in intervals:
                if runs and (runs[-1][1] is None or start <= runs[-1][1]):
                    prev = runs[-1]
                    prev[1] = None if prev[1] is None or end is None else max(prev[1], end)
                    prev[2].append(a)
                else:
                    runs.append([start, end, [a]])
            for start, end, support in runs:
                # Deterministic rendering independent of assertion input order.
                display = min(a.value for a in support)
                output.append(Episode(key, display, start, end,
                                      tuple(sorted(a.assertion_id for a in support)),
                                      tuple(sorted({a.source_id for a in support}))))
    return sorted(output, key=lambda e: (e.key, e.start, e.value))

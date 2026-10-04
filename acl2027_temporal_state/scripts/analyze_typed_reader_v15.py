#!/usr/bin/env python3
"""Replay the post-execution comparison of bound v15/v15.1 JSONL records.

No source document is decoded and neither reader is imported or executed.
Source-derived values are compared internally but never emitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from itertools import zip_longest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMITS = [
    "Post-execution descriptive record comparison, not a new prespecified endpoint.",
    "Typed-record equality on previously processed rows excludes wall time and demonstrates no observed change there; not a proof of general semantic equivalence.",
    "No source values are published here; numeric comparability is not full XBRL/DTS validation or financial correctness.",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repository_path(value):
    path = (ROOT / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    require(path.is_relative_to(ROOT), "public input must be inside repository")
    return path


def load_summary(path, schema):
    summary = json.loads(path.read_text())
    require(summary.get("schema_version") == schema, "unexpected execution summary schema")
    require(summary.get("finished_at_utc"), "execution has no completion receipt")
    protocol_path = repository_path(summary["protocol_path"])
    require(sha256(protocol_path) == summary["protocol_sha256"], "execution protocol binding mismatch")
    protocol = json.loads(protocol_path.read_text())
    require(summary["natural_QA_predictions"] == protocol["natural_QA_predictions"] == 0, "this analysis excludes natural QA")
    require(summary["source_denominator"] == len(summary["records"]) == len(protocol["sources"]) == 12, "source denominator differs from frozen cohort")
    require(summary["expected_nonfraction_denominator"] == protocol["expected_total_nonfraction_count"] == 23651, "occurrence denominator differs from frozen population")
    require([r["source_path"] for r in summary["records"]] == [r["source_path"] for r in protocol["sources"]], "source order mismatch")
    return summary, protocol_path, protocol


class BoundRecords:
    """Stream validated fact pairs and retain only aggregate lookup metadata."""
    def __init__(self, public, expected, external_directory=None):
        artifact = public["external_records"]
        self.path = Path(artifact["path"])
        if external_directory is not None:
            self.path = external_directory / self.path.name
        require(self.path.stat().st_size == artifact["bytes"] and sha256(self.path) == artifact["sha256"], "external artifact byte/digest mismatch")
        self.public, self.expected = public, expected
        self.lookup = Counter()
        self.lookup_probes = []
        self.reader_statuses, self.reference_statuses = Counter(), Counter()
        self.reader_observed = self.reference_observed = 0
        self.fact_count = 0

    def facts(self):
        header_count = 0
        with self.path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                row = json.loads(line)
                kind = row.get("record_type")
                if kind == "source":
                    header_count += 1
                    require(line_number == 1 and header_count == 1 and row["source"] == self.expected, "external source header mismatch")
                elif kind == "fact_pair":
                    require(header_count == 1 and row["fact_ordinal"] == self.fact_count, "external occurrence order mismatch")
                    self.fact_count += 1
                    typed, reference = row["typed"], row["reference"]
                    self.reader_statuses[typed["numeric_status"] if typed is not None else "missing_occurrence"] += 1
                    self.reference_statuses[reference["status"] if reference is not None else "missing_occurrence"] += 1
                    if typed is not None:
                        self.reader_observed += 1
                        require(typed["fact_ordinal"] == row["fact_ordinal"] and typed["source_sha256"] == self.expected["expected_sha256"], "typed occurrence provenance mismatch")
                    if reference is not None:
                        self.reference_observed += 1
                    yield row
                elif kind == "lookup_self_consistency":
                    self.lookup[row["lookup_status"]] += 1
                    self.lookup_probes.append({key: row[key] for key in ("seed_ordinal", "seed_candidate_included", "wrong_source_sha_blocked", "lookup_status")})
                else:
                    require(kind in ("contexts", "units"), "unknown external record kind")
        require(header_count == 1, "external source header absent")
        count = self.expected["expected_nonfraction_count"]
        require(self.fact_count == count == self.public["counts"]["expected_population"], "external fact population mismatch")
        require(self.reader_observed == self.public["counts"]["reader_observed"] and self.reference_observed == self.public["counts"]["reference_observed"], "external/public observed population mismatch")
        require(dict(self.reader_statuses) == self.public["reader_status_counts"] and dict(self.reference_statuses) == self.public["reference_status_counts"], "external/public status counts mismatch")
        require(self.lookup_probes == self.public["lookup_probes"], "external/public lookup records mismatch")


def pair_counts(old, new):
    """Compute counts from actual record equality without expected outcomes."""
    counts = Counter()
    old_reference, new_reference = old["reference"], new["reference"]
    if old_reference is None or new_reference is None:
        counts["reference_records_missing"] += 1
    elif old_reference == new_reference:
        counts["reference_records_unchanged"] += 1
    else:
        counts["reference_records_changed"] += 1
    previous, current = old["typed"], new["typed"]
    if previous is None:
        counts["newly_processed_typed_records" if current is not None else "typed_records_missing_in_both_attempts"] += 1
    else:
        counts["previously_observed_typed_records"] += 1
        counts["previously_observed_typed_records_unchanged" if previous == current else "previously_observed_typed_records_changed"] += 1
        if current is None:
            counts["previously_observed_typed_records_lost"] += 1
    return counts


def analyze(original_path, amended_path, original_external=None, amended_external=None):
    old, old_protocol_path, old_protocol = load_summary(original_path, "typed_reader_execution_v15")
    new, new_protocol_path, new_protocol = load_summary(amended_path, "typed_reader_execution_v15_1")
    require(old_protocol["sources"] == new_protocol["sources"], "cross-attempt source population differs")
    require(new_protocol["parent_protocol"]["sha256"] == sha256(old_protocol_path), "amendment parent protocol differs")
    require(new_protocol["parent_result"]["sha256"] == sha256(original_path), "amendment parent result differs")
    inputs = {str(path.relative_to(ROOT)): sha256(path) for path in (original_path, amended_path, old_protocol_path, new_protocol_path)}
    total, unsupported, unresolved, lookup = Counter(), Counter(), Counter(), Counter()
    per_source = []
    for original, amended, expected in zip(old["records"], new["records"], old_protocol["sources"]):
        require(original["source_sha256"] == amended["source_sha256"] == expected["expected_sha256"], "source identity differs between summaries")
        old_records = BoundRecords(original, expected, original_external)
        new_records = BoundRecords(amended, expected, amended_external)
        local = Counter()
        for previous, current in zip_longest(old_records.facts(), new_records.facts()):
            require(previous is not None and current is not None, "cross-attempt fact count differs")
            require(previous["fact_ordinal"] == current["fact_ordinal"], "cross-attempt occurrence order differs")
            local.update(pair_counts(previous, current))
            typed = current["typed"]
            if typed is not None:
                if typed["numeric_status"] == "unsupported":
                    unsupported.update(typed["unresolved_issues"])
                if typed["binding_status"] == "unresolved":
                    unresolved.update(typed["unresolved_issues"])
        total.update(local)
        lookup.update(new_records.lookup)
        per_source.append({"source_path": expected["source_path"], **dict(local)})
    changed = any(total.get(key, 0) for key in (
        "reference_records_missing", "reference_records_changed", "typed_records_missing_in_both_attempts",
        "previously_observed_typed_records_changed", "previously_observed_typed_records_lost"))
    return {
        "schema_version": "typed_reader_analysis_v15",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "descriptive_cross_attempt_changes_detected" if changed else "passed_descriptive_cross_attempt_replay",
        "input_bindings": inputs,
        "cross_attempt_counts": dict(total),
        "per_source": per_source,
        "unsupported_numeric_issue_counts": dict(unsupported),
        "unresolved_binding_issue_counts": dict(unresolved),
        "lookup_status_counts": dict(lookup),
        "natural_QA_predictions": 0,
        "limits": LIMITS,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-summary", default="results/typed_reader_execution_v15.json")
    parser.add_argument("--amended-summary", default="results/typed_reader_execution_v15_1.json")
    parser.add_argument("--original-external-dir", type=Path)
    parser.add_argument("--amended-external-dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results/typed_reader_analysis_v15.json"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = repository_path(args.output)
    if not args.check:
        require(not output.exists(), "refusing to overwrite existing analysis")
    observed = analyze(repository_path(args.original_summary), repository_path(args.amended_summary), args.original_external_dir, args.amended_external_dir)
    if args.check:
        expected = json.loads(output.read_text())
        expected.pop("created_at_utc", None)
        observed.pop("created_at_utc", None)
        if expected != observed:
            keys = sorted(key for key in set(expected) | set(observed) if expected.get(key) != observed.get(key))
            raise SystemExit("Semantic replay mismatch in fields: " + ", ".join(keys))
        print(json.dumps({"status": "exact_semantic_replay_passed", "output": str(output.relative_to(ROOT)), "output_sha256": sha256(output), "cross_attempt_counts": observed["cross_attempt_counts"]}, sort_keys=True))
    else:
        with output.open("x", encoding="utf-8") as stream:
            json.dump(observed, stream, indent=2)
            stream.write("\n")
        print(json.dumps({"status": "created", "output": str(output.relative_to(ROOT)), "output_sha256": sha256(output)}, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Strict, source-bounded diagnostic scoring. This is not a semantic judge.

References and aliases must be frozen before predictions. A strict mismatch
does not establish an asserted interpretation error. Such errors require a
separate method-masked review. Never use this program to infer global coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import unicodedata

FIELDS = ("entity", "relation", "scope", "time", "comparison", "replacement")
STATUSES = {"resolved", "ambiguous", "insufficient", "ineligible"}


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def reject_constant(value):
    raise ValueError("nonfinite_json_number")


def parse_response(raw, allowed_evidence, arm, previous=None):
    value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    required = {"status", "selected", "candidates"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - (required | {"changes"}):
        raise ValueError("response_fields")
    if value["status"] not in STATUSES:
        raise ValueError("status")
    candidates = value["candidates"]
    if not isinstance(candidates, list) or len(candidates) > 2:
        raise ValueError("candidate_capacity")
    if not candidates and value["status"] != "ineligible":
        raise ValueError("empty_candidates")
    ids = []
    allowed_ids = {"c1", "c2", "n1", "n2"} if arm == "R1" else {"c1", "c2"}

    def evidence(ids_, *, unique=True):
        if not isinstance(ids_, list) or len(ids_) > 8 or not all(isinstance(x, str) for x in ids_):
            raise ValueError("evidence_list")
        if unique and len(set(ids_)) != len(ids_):
            raise ValueError("duplicate_evidence")
        if set(ids_) - set(allowed_evidence):
            raise ValueError("unknown_evidence")

    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"id", "evidence_ids", *FIELDS}:
            raise ValueError("candidate_fields")
        if candidate["id"] not in allowed_ids:
            raise ValueError("candidate_id")
        ids.append(candidate["id"])
        for field in FIELDS:
            item = candidate[field]
            if item is not None and (not isinstance(item, str) or not item.strip()):
                raise ValueError("field_value")
        if candidate["replacement"] not in {"yes", "no", "NA", None}:
            raise ValueError("replacement_value")
        evidence(candidate["evidence_ids"])
        if any(candidate[f] not in {None, "NA"} for f in FIELDS) and not candidate["evidence_ids"]:
            raise ValueError("unsupported_candidate")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_candidate_id")
    if value["status"] == "resolved":
        if value["selected"] not in ids:
            raise ValueError("selected_candidate_missing")
    elif value["selected"] is not None:
        raise ValueError("unresolved_selection")
    return value


def validate_ledger(value, allowed_evidence, arm, previous=None):
    """Ledger defects never change primary interpretation correctness."""
    changes = value.get("changes")
    if not isinstance(changes, list) or len(changes) > 4:
        raise ValueError("changes_capacity")
    if arm != "R1" and changes:
        raise ValueError("unexpected_changes")
    ids = {c["id"] for c in value["candidates"]}
    old_ids = {c["id"] for c in previous["candidates"]} if previous else set()
    ledger_old = []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"old", "new", "action", "evidence_ids"}:
            raise ValueError("change_fields")
        if change["action"] not in {"keep", "revise", "retire", "add"}:
            raise ValueError("change_action")
        for key in ("old", "new"):
            if change[key] is not None and not isinstance(change[key], str):
                raise ValueError("change_identifier")
        links = change["evidence_ids"]
        if not isinstance(links, list) or len(links) > 8 or not all(isinstance(x, str) for x in links):
            raise ValueError("ledger_evidence_list")
        if set(links) - set(allowed_evidence):
            raise ValueError("ledger_unknown_evidence")
        if len(set(links)) != len(links):
            raise ValueError("ledger_duplicate_evidence")
        if change["old"] is not None:
            ledger_old.append(change["old"])
        if change["action"] == "add":
            if change["old"] is not None or change["new"] not in ids:
                raise ValueError("add_links")
        elif change["action"] == "retire":
            if change["old"] is None or change["new"] is not None or change["old"] in ids:
                raise ValueError("retire_links")
        elif change["old"] is None or change["new"] not in ids:
            raise ValueError("change_links")
        if change["action"] == "keep" and change["old"] != change["new"]:
            raise ValueError("keep_identifier_changed")
        if change["action"] == "keep" and previous is not None:
            old = next((c for c in previous["candidates"] if c["id"] == change["old"]), None)
            new = next((c for c in value["candidates"] if c["id"] == change["new"]), None)
            if old is None or new is None or any(old[f] != new[f] for f in FIELDS):
                raise ValueError("keep_assignment_changed")
    if previous is not None and arm == "R1":
        if set(ledger_old) != old_ids or len(ledger_old) != len(old_ids):
            raise ValueError("previous_accounting")
    if arm == "R1" and {c["new"] for c in changes if c["new"] is not None} != ids:
        raise ValueError("active_candidate_accounting")
    return {"ledger_valid": True,
            "unsupported_retirements": sum(x["action"] == "retire" and not x["evidence_ids"] for x in changes)}


def field_match(candidate, name, reference):
    actual = candidate[name]
    state = reference["state"]
    if state == "unknown":
        return actual is None
    if state == "not_applicable":
        return actual == "NA"
    if state != "known":
        raise ValueError("reference_field_state")
    if actual is None or actual == "NA":
        return False
    lexical = normalize(actual) in {normalize(x) for x in reference["accepted_values"]}
    witnesses = reference["evidence_sets"]
    grounded = any(bool(s) and set(s) <= set(candidate["evidence_ids"]) for s in witnesses)
    return lexical and grounded


def score_parsed(value, reference, required_fields):
    status = reference["status"]
    if status == "unscorable":
        return {"strict_correct": None, "reason": "reference_unscorable"}
    if status == "ineligible":
        return {"strict_correct": value["status"] == "ineligible", "reference_status": status,
                "observed_class_recall": None, "unsupported_resolution": value["status"] == "resolved"}
    classes = reference["classes"]
    if not 1 <= len(classes) <= 2:
        raise ValueError("reference_capacity")
    if len({c["class_id"] for c in classes}) != len(classes):
        raise ValueError("reference_duplicate_class_id")
    if not required_fields or set(required_fields) - set(FIELDS):
        raise ValueError("reference_required_fields")
    matches = {}
    field_matches = {}
    for candidate in value["candidates"]:
        row = []
        field_matches[candidate["id"]] = {}
        for cls in classes:
            fields = {f: field_match(candidate, f, cls["fields"][f]) for f in required_fields}
            field_matches[candidate["id"]][cls["class_id"]] = fields
            if all(fields.values()):
                row.append(cls["class_id"])
        matches[candidate["id"]] = row
    covered = {x for values in matches.values() for x in values}
    correct = False
    if value["status"] == status:
        if status == "resolved":
            correct = bool(matches.get(value["selected"]))
        elif status == "ambiguous":
            correct = len(covered) == len(classes)
        elif status == "insufficient":
            correct = bool(covered)
    return {"strict_correct": correct, "reference_status": status,
            "observed_class_recall": len(covered) / len(classes),
            "matched_classes_by_candidate": matches, "field_matches": field_matches,
            "unsupported_resolution": value["status"] == "resolved" and status in {"ambiguous", "insufficient"},
            "asserted_interpretation_error": None,
            "semantic_review_required_for_error_or_win": True}


def score_raw(raw, reference, required_fields, evidence_ids, arm, previous=None, transport_status="completed",
              previous_evidence_ids=None):
    if reference["status"] == "unscorable":
        return {"strict_correct": None, "reason": "reference_unscorable",
                "transport_status": transport_status, "asserted_interpretation_error": None}
    if transport_status != "completed":
        return {"strict_correct": False, "execution_failure": transport_status,
                "asserted_interpretation_error": None}
    try:
        value = parse_response(raw, evidence_ids, arm, previous)
    except (ValueError, TypeError, KeyError) as error:
        return {"strict_correct": False, "parse_failure": str(error),
                "asserted_interpretation_error": None}
    # Previous may be raw text or a parsed transport object. Validate its core
    # independently. Malformed drafts impose no preservation obligation.
    parsed_previous = None
    previous_boundary_missing = previous is not None and previous_evidence_ids is None
    if previous is not None:
        try:
            previous_raw = previous if isinstance(previous, str) else json.dumps(previous)
            if previous_boundary_missing:
                raise ValueError("missing_previous_evidence_boundary")
            parsed_previous = parse_response(previous_raw, previous_evidence_ids, "I0")
        except (ValueError, TypeError, KeyError):
            pass
    result = score_parsed(value, reference, required_fields)
    try:
        if previous_boundary_missing:
            raise ValueError("missing_previous_evidence_boundary")
        result.update(validate_ledger(value, evidence_ids, arm, parsed_previous))
    except (ValueError, TypeError, KeyError) as error:
        result.update({"ledger_valid": False, "ledger_failure": str(error)})
    result["ledger_affects_primary_score"] = False
    return result


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path,
                        help="JSONL scored packets with raw, reference, required_fields, evidence_ids, arm")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    for line in args.inputs.read_text().splitlines():
        row = json.loads(line)
        result = score_raw(**{k: row[k] for k in ("raw", "reference", "required_fields", "evidence_ids", "arm")},
                           previous=row.get("previous"), transport_status=row.get("transport_status", "completed"),
                           previous_evidence_ids=row.get("previous_evidence_ids"))
        rows.append({"id": row["id"], "arm": row["arm"], **result})
    receipt = {"schema_version": "strict_interpretation_scores_v26", "input_sha256": digest(args.inputs),
               "script_sha256": digest(__file__), "semantic_judge": False, "rows": rows}
    with args.output.open("x") as stream:
        json.dump(receipt, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()

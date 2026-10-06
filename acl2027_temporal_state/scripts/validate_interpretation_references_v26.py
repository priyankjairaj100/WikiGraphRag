#!/usr/bin/env python3
"""Validate v26 references against all frozen questions and delivered packs.

This reads no benchmark answers or model outputs. Structural validity does not
establish entailment, source sufficiency, independent human review, or coverage.
Use this before freezing prediction requests. Inputs are never changed.

Example:
  python3 scripts/validate_interpretation_references_v26.py \
    --roster data/tempo_v26/roster.json --packs /external/packs.jsonl \
    --references /external/references_01.json /external/references_02.json \
    --output /external/reference_validation.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unicodedata

FIELDS = ("entity", "relation", "scope", "time", "comparison", "replacement")
ELIGIBLE = {
    "eligible_resolved": "resolved",
    "eligible_ambiguous": "ambiguous",
    "eligible_insufficient": "insufficient",
}
EXCLUDED = {
    "ineligible_no_temporal_target": {"ineligible", "unscorable"},
    "ineligible_representation_capacity": {"ineligible", "unscorable"},
    "unresolved_reference": {"unscorable"},
    "source_access_failure": {"unscorable"},
}
REFERENCE_KEYS = {
    "item_id", "query_sha256", "c0_sha256", "c1_sha256", "eligibility",
    "required_fields", "c0", "c1", "limitations", "author_role", "reviewer_role",
    "review_receipt_sha256",
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("nonfinite_json_number")


def strict_json(text):
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)


def read_records(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        value = strict_json(text)
    except json.JSONDecodeError:
        lines = text.splitlines()
        need(lines and all(line.strip() for line in lines), "blank_jsonl_line")
        value = [strict_json(line) for line in lines]
    if isinstance(value, dict):
        containers = [key for key in ("items", "records") if key in value]
        need(len(containers) <= 1, "ambiguous_record_container")
        value = value[containers[0]] if containers else [value]
    need(isinstance(value, list) and all(isinstance(row, dict) for row in value), "record_container")
    return value


def index_rows(rows, kind):
    need(isinstance(rows, list), kind + "_list")
    result = {}
    for row in rows:
        need(isinstance(row, dict) and nonempty(row.get("item_id")), kind + "_item_id")
        item = row["item_id"]
        need(item not in result, "duplicate_" + kind + "_id:" + item)
        result[item] = row
    return result


def validate_pack(pack, where):
    need(isinstance(pack, dict), where + ":pack_object")
    need({"text", "sha256", "evidence_ids", "units"} <= set(pack), where + ":pack_fields")
    need(isinstance(pack["text"], str), where + ":pack_text")
    need(is_hash(pack["sha256"]) and text_hash(pack["text"]) == pack["sha256"], where + ":pack_hash")
    ids, units = pack["evidence_ids"], pack["units"]
    need(isinstance(ids, list) and all(isinstance(x, str) and re.fullmatch(r"E[0-9]{2}", x) for x in ids),
         where + ":evidence_ids")
    need(len(set(ids)) == len(ids), where + ":duplicate_evidence_id")
    need(isinstance(units, list) and len(units) == len(ids), where + ":units")
    raw = pack["text"].encode("utf-8")
    cursor = 0
    unit_map = {}
    # Use exact byte lengths. Source text can itself resemble an evidence header.
    for index, (eid, unit) in enumerate(zip(ids, units)):
        need(isinstance(unit, dict), where + ":unit_object")
        keys = {"evidence_id", "source_id", "source_sha256", "byte_start", "byte_stop", "span_sha256"}
        need(keys <= set(unit) and unit["evidence_id"] == eid, where + ":unit_fields")
        need(nonempty(unit["source_id"]), where + ":source_id")
        need(is_hash(unit["source_sha256"]) and is_hash(unit["span_sha256"]), where + ":unit_hash")
        a, b = unit["byte_start"], unit["byte_stop"]
        need(type(a) is int and type(b) is int and 0 <= a < b, where + ":unit_offsets")
        if index:
            need(raw[cursor:cursor + 2] == b"\n\n", where + ":unit_separator")
            cursor += 2
        header = f"[{eid} | {unit['source_id']}]\n".encode("utf-8")
        need(raw[cursor:cursor + len(header)] == header, where + ":rendered_evidence_header")
        cursor += len(header)
        span = raw[cursor:cursor + b - a]
        need(len(span) == b - a and hashlib.sha256(span).hexdigest() == unit["span_sha256"],
             where + ":rendered_span_hash")
        cursor += b - a
        unit_map[eid] = unit
    need(cursor == len(raw), where + ":unaccounted_pack_text")
    return unit_map


def validate_field(field, name, allowed_ids, where):
    need(isinstance(field, dict) and set(field) == {"state", "accepted_values", "evidence_sets"},
         where + ":field_schema")
    state = field["state"]
    need(isinstance(state, str) and state in {"known", "unknown", "not_applicable"}, where + ":field_state")
    values, witnesses = field["accepted_values"], field["evidence_sets"]
    need(isinstance(values, list) and all(nonempty(v) for v in values), where + ":accepted_values")
    normalized_values = [normalized(v) for v in values]
    need(len(set(normalized_values)) == len(values), where + ":duplicate_normalized_alias")
    need(isinstance(witnesses, list), where + ":evidence_sets")
    seen = set()
    for witness in witnesses:
        need(isinstance(witness, list) and 1 <= len(witness) <= 8
             and all(isinstance(eid, str) for eid in witness), where + ":witness_set")
        need(len(set(witness)) == len(witness), where + ":duplicate_witness_id")
        need(set(witness) <= allowed_ids, where + ":unknown_evidence")
        frozen = frozenset(witness)
        need(frozen not in seen, where + ":duplicate_witness_set")
        seen.add(frozen)
    if state == "known":
        need(values and witnesses, where + ":known_requires_values_and_witness")
        need("na" not in normalized_values, where + ":reserved_known_alias")
        if name == "replacement":
            need(set(normalized_values) <= {"yes", "no"}, where + ":replacement_alias")
    else:
        need(not values, where + ":nonknown_aliases")


def joint_witness_possible(fields, required):
    alternatives = {frozenset()}
    for name in required:
        field = fields[name]
        if field["state"] != "known":
            continue
        expanded = {old | frozenset(witness) for old in alternatives for witness in field["evidence_sets"]
                    if len(old | frozenset(witness)) <= 8}
        if not expanded:
            return False
        # Supersets never improve a later union's fit within the output cap.
        alternatives = {candidate for candidate in expanded
                        if not any(other < candidate for other in expanded)}
    return True


def validate_view(view, required, allowed_ids, where):
    need(isinstance(view, dict) and set(view) == {"status", "classes"}, where + ":view_schema")
    status, classes = view["status"], view["classes"]
    need(isinstance(status, str) and status in {"resolved", "ambiguous", "insufficient", "ineligible", "unscorable"},
         where + ":reference_status")
    need(isinstance(classes, list), where + ":classes")
    if status in {"ineligible", "unscorable"}:
        need(not classes, where + ":excluded_classes")
        return
    need(required, where + ":required_fields_empty")
    need(1 <= len(classes) <= 2, where + ":class_capacity")
    if status == "resolved":
        need(len(classes) == 1, where + ":resolved_class_count")
    if status == "ambiguous":
        need(len(classes) == 2, where + ":ambiguous_class_count")
    seen = set()
    for cls in classes:
        need(isinstance(cls, dict) and set(cls) == {"class_id", "fields"}, where + ":class_schema")
        need(nonempty(cls["class_id"]), where + ":class_id")
        need(cls["class_id"] not in seen, where + ":duplicate_class_id")
        seen.add(cls["class_id"])
        fields = cls["fields"]
        need(isinstance(fields, dict) and set(fields) == set(FIELDS), where + ":class_fields")
        for name in FIELDS:
            validate_field(fields[name], name, allowed_ids, where + ":" + cls["class_id"] + ":" + name)
        states = {fields[name]["state"] for name in required}
        need("known" in states, where + ":required_known_field_missing")
        if status == "resolved":
            need("unknown" not in states, where + ":resolved_required_unknown")
        if status == "insufficient":
            need("unknown" in states, where + ":insufficient_required_unknown_missing")
        need(joint_witness_possible(fields, required), where + ":joint_witness_exceeds_output_capacity")
    if status == "ambiguous":
        a, b = (cls["fields"] for cls in classes)
        incompatible = any(
            a[f]["state"] == b[f]["state"] == "known"
            and not ({normalized(v) for v in a[f]["accepted_values"]}
                     & {normalized(v) for v in b[f]["accepted_values"]})
            for f in required)
        need(incompatible, where + ":ambiguity_without_known_incompatibility")


def validate_references(references, packs, roster, *, expected_count=60):
    reference_map = index_rows(references, "reference")
    pack_map = index_rows(packs, "pack")
    roster_map = index_rows(roster, "roster")
    need(len(roster_map) == expected_count, "frozen_roster_count")
    need(set(reference_map) == set(roster_map), "reference_roster_mismatch")
    need(set(pack_map) == set(roster_map), "pack_roster_mismatch")
    counts = Counter()
    statuses = {key: Counter() for key in ("c0", "c1")}
    for item, reference in reference_map.items():
        need(set(reference) == REFERENCE_KEYS, item + ":reference_schema")
        packet, question = pack_map[item], roster_map[item]
        query_hash = question.get("query_sha256")
        need(is_hash(query_hash), item + ":roster_query_hash")
        need(isinstance(packet.get("query"), str) and text_hash(packet["query"]) == query_hash,
             item + ":packet_query_bytes")
        need(packet.get("query_sha256") == reference["query_sha256"] == query_hash,
             item + ":query_binding")
        unit_maps = {}
        for key in ("c0", "c1"):
            need(key in packet, item + ":missing_pack:" + key)
            unit_maps[key] = validate_pack(packet[key], item + ":" + key)
            need(reference[key + "_sha256"] == packet[key]["sha256"], item + ":reference_pack_hash:" + key)
        need(all(eid in unit_maps["c1"] and unit == unit_maps["c1"][eid]
                 for eid, unit in unit_maps["c0"].items()), item + ":c0_not_fixed_subset_of_c1")
        required = reference["required_fields"]
        need(isinstance(required, list) and all(isinstance(f, str) for f in required), item + ":required_fields_type")
        need(len(set(required)) == len(required) and set(required) <= set(FIELDS), item + ":required_fields")
        eligibility = reference["eligibility"]
        need(isinstance(eligibility, str) and eligibility in set(ELIGIBLE) | set(EXCLUDED), item + ":eligibility")
        need(isinstance(reference["limitations"], list)
             and all(nonempty(value) for value in reference["limitations"]), item + ":limitations")
        need(nonempty(reference["author_role"]) and nonempty(reference["reviewer_role"]), item + ":review_roles")
        need(reference["author_role"] != reference["reviewer_role"], item + ":review_roles_not_distinct")
        need(is_hash(reference["review_receipt_sha256"]), item + ":review_receipt_hash")
        for key in ("c0", "c1"):
            validate_view(reference[key], required, {"Q", *packet[key]["evidence_ids"]}, item + ":" + key)
            statuses[key][reference[key]["status"]] += 1
        if eligibility in ELIGIBLE:
            need(packet.get("status") == "ready", item + ":eligible_pack_not_ready")
            need(reference["c1"]["status"] == ELIGIBLE[eligibility], item + ":eligibility_c1_status")
            need(reference["c0"]["status"] in {"resolved", "ambiguous", "insufficient"},
                 item + ":eligible_c0_unreviewed")
        else:
            need(reference["limitations"], item + ":exclusion_reason_missing")
            need(all(reference[key]["status"] in EXCLUDED[eligibility] for key in ("c0", "c1")),
                 item + ":excluded_status")
        counts[eligibility] += 1
    return {
        "schema_version": "interpretation_reference_validation_v26",
        "status": "structurally_valid_not_semantically_adjudicated",
        "roster_count": len(roster_map), "references_count": len(reference_map),
        "all_roster_items_accounted": True, "eligibility_counts": dict(sorted(counts.items())),
        "reference_status_counts": {key: dict(sorted(value.items())) for key, value in statuses.items()},
        "query_and_pack_hashes_verified": True, "c0_fixed_subset_c1_verified": True,
        "witness_ids_bound_to_delivered_packs": True, "required_known_field_enforced": True,
        "reference_entailment_verified": False, "independent_human_gold_verified": False,
        "review_receipt_contents_verified": False, "global_coverage_verified": False,
        "benchmark_answers_read": False, "model_predictions_read": False,
        "preprediction_chronology_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--roster", required=True, type=Path)
    parser.add_argument("--packs", required=True, type=Path)
    parser.add_argument("--references", required=True, nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    references = [row for path in args.references for row in read_records(path)]
    result = validate_references(references, read_records(args.packs), read_records(args.roster))
    result["input_sha256"] = {
        "roster": file_hash(args.roster), "packs": file_hash(args.packs),
        "references": [{"file": path.name, "sha256": file_hash(path)} for path in args.references],
    }
    result["script_sha256"] = file_hash(__file__)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

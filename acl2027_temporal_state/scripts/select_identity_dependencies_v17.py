#!/usr/bin/env python3
"""Freeze then select bounded identity-render candidates; no QA authoring."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import unicodedata
from lxml import etree

import build_table_views_v16 as views

ROOT = Path(__file__).resolve().parents[1]
XH, IX = views.XH, views.IX
FIELDS = ("EntityRegistrantName", "DocumentType", "DocumentPeriodEndDate")
BLOCK_TAGS = {XH + t for t in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "table")}
MATCH_TAGS = BLOCK_TAGS | {XH + t for t in ("span", "font", "b", "strong", "em", "i", "td", "th")}
RULE = {
    "required_fields": list(FIELDS), "maximum_candidates_per_field": 2,
    "block_bytes_maximum": 16_384, "block_text_characters_maximum": 1_500,
    "block_elements_maximum": 256, "block_numeric_occurrences_maximum": 16,
    "ancestor_steps_maximum": 8, "maximum_unique_blocks_per_source": 6,
    "source_bytes_maximum": 25 * 1024 * 1024, "external_bytes_maximum": 25_000_000,
    "external_metadata_reserve_bytes": 65_536,
    "normalization": "NFKC_then_casefold_then_Unicode_whitespace_collapse",
    "selection": "first_two_unique_eligible_blocks_in_XML_order_per_field",
    "issuer_fallback": "only_when_all_dei_issuer_nodes_recognizably_hidden_exact_normalized_visible_text_no_aliases",
    "visibility": "no_recognized_hiding_cue_in_node_ancestors_or_selected_block_subtree_no_computed_CSS_claim",
    "complete_blocks": True, "rewrite_source": False,
}
CODE = ("scripts/select_identity_dependencies_v17.py", "scripts/build_table_views_v16.py",
        "src/temporal_state/typed_reader_v15_1.py", "tests/test_identity_dependencies_v17.py",
        "docs/identity_dependency_selection_contract_v17.txt")
INPUTS = ("data/reader_binding_v16/table_view_protocol_v16_1.json",
          "results/reader_pool_v16_1.json", "results/reader_source_card_provenance_v17.json",
          "data/fresh_source_audit_v14/source_identity_structure.json")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def text_of(node):
    # Whole original subtree text; only used after hidden/script/style rejection.
    return "".join(node.itertext())


def hidden(node, paths):
    if any(p.tag in (IX + "header", IX + "hidden") for p in views.ancestors_including(node)):
        return True
    return views.visibility(node, paths)["status"] != "no_recognized_hiding_cue"


def dei_field(node):
    if node.tag != IX + "nonNumeric":
        return None
    value = node.get("name", "")
    parts = value.split(":")
    if len(parts) != 2 or parts[1] not in FIELDS:
        return None
    namespace = node.nsmap.get(parts[0], "")
    if not namespace.startswith(("http://xbrl.sec.gov/dei/", "https://xbrl.sec.gov/dei/")):
        return None
    return parts[1]


def assess_block(node, paths, spans, source):
    reasons = []
    bound = views.anchor(node, paths, spans, source)
    if bound["byte_stop"] - bound["byte_start"] > RULE["block_bytes_maximum"]:
        reasons.append("block_bytes_cap")
    nodes = [n for n in node.iter() if isinstance(n.tag, str)]
    if len(nodes) > RULE["block_elements_maximum"]:
        reasons.append("block_elements_cap")
    if sum(n.tag == IX + "nonFraction" for n in nodes) > RULE["block_numeric_occurrences_maximum"]:
        reasons.append("block_numeric_occurrences_cap")
    if any(n.tag in views.SKIP_TEXT for n in nodes):
        reasons.append("script_or_style_inside_block")
    if any(hidden(n, paths) for n in nodes):
        reasons.append("recognized_hiding_cue")
    if any(n.tag == views.TABLE for n in nodes[1:]):
        reasons.append("nested_table_inside_block")
    raw_text = text_of(node)
    if len(" ".join(raw_text.split())) > RULE["block_text_characters_maximum"]:
        reasons.append("block_text_cap")
    if not normalize(raw_text):
        reasons.append("empty_text")
    return reasons, bound, raw_text


def containing_block(node, paths, spans, source):
    considered = []
    for step, ancestor in enumerate(views.ancestors_including(node)):
        if step >= RULE["ancestor_steps_maximum"]:
            break
        if ancestor.tag not in BLOCK_TAGS:
            continue
        reasons, anchor, raw_text = assess_block(ancestor, paths, spans, source)
        considered.extend(reasons)
        if not reasons:
            return ancestor, anchor, raw_text, considered
    return None, None, None, considered or ["no_small_containing_block_within_ancestor_bound"]


def select_source(source, source_record):
    if len(source) > RULE["source_bytes_maximum"] or sha(source) != source_record["source_sha256"]:
        raise ValueError("source_size_or_hash_mismatch")
    root, paths, spans = views.parse_bound(source)
    ordered = list(paths)
    fields = {field: [n for n in ordered if dei_field(n) == field] for field in FIELDS}
    blocks, field_records, private_fields = {}, [], []
    for field in FIELDS:
        nodes = fields[field]
        values = {normalize(text_of(n)) for n in nodes}
        rejects = Counter()
        mechanism = "visible_dei"
        if not nodes:
            candidates, unavailable = [], "no_dei_field"
        elif len(values) != 1 or "" in values:
            candidates, unavailable = [], "conflicting_or_empty_dei_values"
        else:
            visible = [n for n in nodes if not hidden(n, paths)
                       and not any(hidden(d, paths) for d in n.iterdescendants() if isinstance(d.tag, str))]
            rejects["dei_nodes_with_recognized_hiding"] = len(nodes) - len(visible)
            candidates, unavailable = visible, "no_eligible_visible_dei_containing_block"
            if field == "EntityRegistrantName" and not visible and all(hidden(n, paths) for n in nodes):
                mechanism = "hidden_dei_issuer_exact_visible_text_fallback"
                target = next(iter(values))
                candidates = []
                for node in ordered:
                    if node.tag not in MATCH_TAGS or hidden(node, paths):
                        continue
                    span = spans[paths[node]]
                    if span.stop - span.start > RULE["block_bytes_maximum"]:
                        continue
                    if any(hidden(d, paths) for d in node.iterdescendants() if isinstance(d.tag, str)):
                        continue
                    if normalize(text_of(node)) == target:
                        candidates.append(node)
                unavailable = "no_exact_visible_issuer_match_with_eligible_block"
        eligible = {}
        match_records = []
        for node in candidates:
            block, anchor, raw_text, reasons = containing_block(node, paths, spans, source)
            if block is None:
                rejects.update(reasons)
                continue
            key = paths[block]
            if key not in eligible:
                eligible[key] = (block, anchor, raw_text)
                match_records.append((key, node))
        ranked = sorted(eligible, key=lambda key: eligible[key][1]["byte_start"])
        selected = ranked[:RULE["maximum_candidates_per_field"]]
        selected_ids = []
        for key in selected:
            block, anchor, raw_text = eligible[key]
            block_id = "identity_" + str(anchor["byte_start"])
            selected_ids.append(block_id)
            if key not in blocks:
                blocks[key] = {"block_id": block_id, "dom_path": key, "anchor": anchor,
                               "role": "identity_dependency_candidate", "identity_fields": [],
                               "visibility_status": "no_recognized_hiding_cue_not_CSS_certified",
                               "rendered": False, "identity_admitted": False}
            blocks[key]["identity_fields"].append(field)
            matches = [n for k, n in match_records if k == key]
            private_fields.append({"field": field, "block_id": block_id,
                                   "selection_mechanism": mechanism, "whole_block_text": raw_text,
                                   "matching_nodes": [{"dom_path": paths[n],
                                                       "anchor": views.anchor(n, paths, spans, source),
                                                       "source_text": text_of(n)} for n in matches]})
        field_records.append({"field": field, "dei_node_count": len(nodes),
                              "distinct_normalized_dei_value_count": len(values),
                              "selection_mechanism": mechanism,
                              "syntactically_eligible_unique_blocks": len(ranked),
                              "selected_candidate_blocks": selected_ids,
                              "eligible_not_selected": max(0, len(ranked) - len(selected)),
                              "rejection_counts": dict(sorted(rejects.items())),
                              "status": "candidates_pending_render_and_review" if selected else "unavailable",
                              "unavailable_reason": None if selected else unavailable})
    block_records = sorted(blocks.values(), key=lambda b: b["anchor"]["byte_start"])
    if len(block_records) > RULE["maximum_unique_blocks_per_source"]:
        raise ValueError("block_count_cap")
    public = dict(source_record, blocks=block_records, identity_fields=field_records)
    private = {"source_path": source_record["source_path"], "source_sha256": source_record["source_sha256"],
               "identity_field_candidates": private_fields, "questions_or_references": None}
    return public, private


def freeze(output, review_path):
    review = json.loads(Path(review_path).read_text())
    expected = {path: digest(ROOT / path) for path in CODE}
    if review.get("status") != "passed_authored_review" or review.get("open_blockers") != []:
        raise ValueError("independent_review_not_closed")
    if any(review.get("code_bindings", {}).get(path) != checksum for path, checksum in expected.items()):
        raise ValueError("independent_review_code_binding_mismatch")
    parent = json.loads((ROOT / INPUTS[0]).read_text())
    sources = [{"source_path": s["source_path"], "external_filename": s["external_filename"],
                "source_sha256": s["expected_sha256"], "source_bytes": s["expected_bytes"]}
               for s in parent["sources"]]
    if len(sources) != 12 or len({s["source_path"] for s in sources}) != 12:
        raise ValueError("source_denominator")
    protocol = {"schema_version": "identity_dependency_selection_v17",
                "frozen_at_utc": utc(), "source_selection_performed": False,
                "rule": RULE, "sources": sources, "code_bindings": expected,
                "input_bindings": {p: digest(ROOT / p) for p in INPUTS},
                "review_path": str(Path(review_path).resolve().relative_to(ROOT)),
                "review_sha256": digest(review_path),
                "runtime": {"python": sys.version, "lxml": list(etree.LXML_VERSION)},
                "source_denominator": 12, "required_field_denominator": 36,
                "natural_QA_predictions": 0, "complete_evidence_claim": False}
    write_new(output, protocol)
    return protocol


def run(protocol_path, source_root, external_output, output):
    protocol = json.loads(Path(protocol_path).read_text())
    if protocol["schema_version"] != "identity_dependency_selection_v17" or protocol["rule"] != RULE:
        raise ValueError("protocol_mismatch")
    if set(protocol.get("code_bindings", {})) != set(CODE) or set(protocol.get("input_bindings", {})) != set(INPUTS):
        raise ValueError("incomplete_frozen_dependency_population")
    for key in ("code_bindings", "input_bindings"):
        if any(digest(ROOT / p) != value for p, value in protocol[key].items()):
            raise ValueError("frozen_dependency_changed")
    review_relative = Path(protocol["review_path"])
    if review_relative.is_absolute() or ".." in review_relative.parts:
        raise ValueError("review_path_not_confined")
    if digest(ROOT / review_relative) != protocol["review_sha256"]:
        raise ValueError("review_changed")
    review = json.loads((ROOT / review_relative).read_text())
    if (review.get("status") != "passed_authored_review" or review.get("open_blockers") != []
            or any(review.get("code_bindings", {}).get(p) != checksum
                   for p, checksum in protocol["code_bindings"].items())):
        raise ValueError("independent_review_not_closed_or_mismatched")
    parent = json.loads((ROOT / INPUTS[0]).read_text())
    expected_sources = [{"source_path": s["source_path"], "external_filename": s["external_filename"],
                         "source_sha256": s["expected_sha256"], "source_bytes": s["expected_bytes"]}
                        for s in parent["sources"]]
    if (len(expected_sources) != 12 or len({s["source_path"] for s in expected_sources}) != 12
            or protocol.get("sources") != expected_sources
            or type(protocol.get("source_denominator")) is not int or protocol["source_denominator"] != 12
            or type(protocol.get("required_field_denominator")) is not int or protocol["required_field_denominator"] != 36):
        raise ValueError("frozen_source_or_field_denominator_mismatch")
    if protocol["runtime"] != {"python": sys.version, "lxml": list(etree.LXML_VERSION)}:
        raise ValueError("runtime_changed")
    external_output, source_root = Path(external_output), Path(source_root).resolve()
    if external_output.exists() or external_output.is_symlink() or Path(output).exists():
        raise FileExistsError("attempt_paths_exist")
    if external_output.resolve().is_relative_to(ROOT.parent):
        raise ValueError("private_output_must_be_external")
    for record in protocol["sources"]:
        filename = record["external_filename"]
        if Path(filename).name != filename:
            raise ValueError("unsafe_source_filename")
        path = source_root / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record["source_bytes"] or digest(path) != record["source_sha256"]:
            raise ValueError("source_preflight_mismatch")
    external_output.mkdir(parents=True, exist_ok=False)
    records, artifacts, failures = [], [], []
    total = 0
    for source_index, record in enumerate(protocol["sources"]):
        try:
            public, private = select_source((source_root / record["external_filename"]).read_bytes(), record)
            payload = (json.dumps(private, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if total + len(payload) > RULE["external_bytes_maximum"] - RULE["external_metadata_reserve_bytes"]:
                raise ValueError("external_byte_cap")
            filename = "source_" + str(source_index).zfill(2) + ".identity_candidates.json"
            with (external_output / filename).open("xb") as stream:
                stream.write(payload)
            total += len(payload)
            artifacts.append({"source_index": source_index, "filename": filename,
                              "bytes": len(payload), "sha256": sha(payload)})
            public["processing_status"] = "completed"
            records.append(public)
        except Exception as exc:
            failures.append({"source_index": source_index, "error_type": type(exc).__name__,
                             "reason": str(exc)[:160] if isinstance(exc, ValueError) else "source_processing_failed"})
            records.append(dict(record, processing_status="failed", blocks=[], identity_fields=[
                {"field": f, "status": "unavailable", "unavailable_reason": "source_processing_failed",
                 "selected_candidate_blocks": []} for f in FIELDS]))
    result = {"schema_version": "identity_dependency_candidates_v17", "finished_at_utc": utc(),
              "status": "completed" if not failures else "completed_with_source_failures",
              "protocol_sha256": digest(protocol_path), "source_denominator": 12,
              "required_field_denominator": 36, "sources": records, "source_failures": failures,
              "selected_unique_blocks": sum(len(s["blocks"]) for s in records),
              "fields_with_candidates": sum(f["status"] == "candidates_pending_render_and_review"
                                            for s in records for f in s["identity_fields"]),
              "external_directory": str(external_output.resolve()), "external_bytes": total,
              "external_artifacts": artifacts, "rendered_blocks": 0, "admitted_identity_cards": 0,
              "questions_authored": 0, "references_authored": 0, "natural_QA_predictions": 0,
              "visibility_limit": "Syntactic cues only; all candidate blocks require actual render and full original CSS/source review."}
    write_new(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--protocol", required=True)
    f.add_argument("--review", required=True)
    r = sub.add_parser("run")
    r.add_argument("--protocol", required=True)
    r.add_argument("--sources", required=True)
    r.add_argument("--external-output", required=True)
    r.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        value = freeze(args.protocol, args.review)
        print(json.dumps({"status": "frozen", "sources": value["source_denominator"]}))
    else:
        value = run(args.protocol, args.sources, args.external_output, args.output)
        print(json.dumps({key: value[key] for key in ("status", "source_denominator", "required_field_denominator",
                                                     "selected_unique_blocks", "fields_with_candidates", "external_bytes")}))

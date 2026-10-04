#!/usr/bin/env python3
"""Describe frozen identity-card visibility provenance without emitting values.

Reads only existing public identity metadata and the producer code. Does not open
source bodies, render, create questions/references, or call any model.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path


FIELDS = ("EntityRegistrantName", "DocumentType", "DocumentPeriodEndDate",
          "DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus", "EntityCentralIndexKey")
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path,
                    default=ROOT / "results/reader_source_card_provenance_v17.json")
    args = ap.parse_args()
    identity_path = ROOT / "data/fresh_source_audit_v14/source_identity_structure.json"
    selector_path = ROOT / "scripts/select_reader_pool_v16.py"
    data = json.loads(identity_path.read_text())
    assert data["schema_version"] == "fresh_source_identity_structure_v14"
    records = data["records"]
    assert len(records) == 12
    tree = ast.parse(selector_path.read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "source_card")
    strings = {node.value for node in ast.walk(function)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    flags = ("in_ix_header_or_hidden", "hidden_by_explicit_inline_style", "visibility_limit")
    assert not any(flag in strings for flag in flags)
    assert {"value", "identity_facts", "document_fiscal_year_values"} <= strings
    total, hidden, style = Counter(), Counter(), Counter()
    per_source = []
    for record in records:
        counts, hidden_counts, style_counts = Counter(), Counter(), Counter()
        for fact in record["identity_facts"]:
            field = fact["name"].split(":")[-1]
            if field not in FIELDS:
                continue
            # The value/context/date strings are never selected for output.
            assert type(fact[flags[0]]) is bool and type(fact[flags[1]]) is bool
            counts[field] += 1
            total[field] += 1
            if fact[flags[0]]:
                hidden_counts[field] += 1
                hidden[field] += 1
            if fact[flags[1]]:
                style_counts[field] += 1
                style[field] += 1
        per_source.append({"source_path": record["source_path"],
                           "source_sha256": record["source_sha256"],
                           "copied_field_occurrence_counts": dict(sorted(counts.items())),
                           "in_ix_header_or_hidden_counts": dict(sorted(hidden_counts.items())),
                           "explicit_inline_hiding_counts": dict(sorted(style_counts.items()))})
    receipt = {
        "schema_version": "reader_source_card_provenance_audit_v17",
        "status": "completed_descriptive_metadata_audit_not_visual_admission",
        "inputs": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)}
                   for p in (identity_path, selector_path, Path(__file__).resolve())],
        "source_bodies_opened": 0, "identity_value_strings_emitted": 0,
        "source_quantity_values_inspected": False,
        "questions_or_references_authored": False, "model_calls": 0,
        "source_card_function_reads_visibility_flags": False,
        "source_card_function_copies_unfiltered_DEI_values": True,
        "source_count": len(records),
        "copied_field_occurrence_counts": dict(sorted(total.items())),
        "in_ix_header_or_hidden_counts": dict(sorted(hidden.items())),
        "explicit_inline_hiding_counts": dict(sorted(style.items())),
        "records": per_source,
        "interpretation": [
            "The original cards are mechanical DEI metadata, not visibly reviewed author cards.",
            "Absence of these syntactic cues does not certify computed CSS visibility.",
            "The inventory retains DEI DOM locators and byte-span hashes for provenance; hidden-node rendering is not visible corroboration.",
            "Before authoring, require inspected visible issuer/report/date support for all twelve sources; omit hidden-only optional fields.",
            "Additional cover/heading render blocks need a separate frozen supplement; preserve the existing 101-block render denominator.",
            "Identity admission alone does not certify quantity periods, populations, units or complete evidence."
        ],
    }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"path": str(args.output), "sha256": digest(args.output),
                      "source_count": len(records), "source_bodies_opened": 0}))


if __name__ == "__main__":
    main()

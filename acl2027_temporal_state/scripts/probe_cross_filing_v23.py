#!/usr/bin/env python3
"""Replay six source-reviewed cross-filing development reporting questions.

This is an audit with disclosed source-reviewed occurrence selections, not an
automatic natural-language question proposer or a retrieval policy evaluation.
Raw documents remain external. The protocol predates inspection of the values;
the selected occurrence ordinals below were assigned during source review.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from temporal_state.typed_reader_v15_1 import _parse, read_inline_xbrl

PROTOCOL = ROOT / "data/empirical_v23/cross_filing_probe_protocol.json"
PROTOCOL_SHA256 = "cffbf0078229aa45e29ade99f05eb1acc9c8193a4532b7f9c44686e18c8a099c"
SELECTIONS = {
    "PFE_2023.html": 31, "PFE_2024.html": 35,
    "NFLX_2022.html": 2, "NFLX_2023.html": 5,
    "NVDA_2022.html": 2, "NVDA_2023.html": 3,
    "LLY_2023.html": 2, "LLY_2024.html": 4,
    "BRK-B_2022.html": 138, "BRK-B_2023.html": 133,
    "SLB_2022.html": 38, "SLB_2023.html": 9,
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def text(node):
    return " ".join(node.text().split())


def eligible(fact):
    return (fact["status"] == "normalized"
            and fact["binding_status"] == "reported_aspects_resolved"
            and all(fact["resolved_aspects"].values())
            and fact["visibility"] != "hidden_markup")


def inspect_source(metadata, external):
    filename = metadata["external_filename"]
    source = (external / filename).read_bytes()
    assert digest(source) == metadata["expected_sha256"], filename
    document = read_inline_xbrl(source, source_version=filename)
    selected = document["facts"][SELECTIONS[filename]]
    assert eligible(selected) and selected["reported_aspects"]["dimensions"] == []
    nodes = _parse(source)
    node = next(n for n in nodes if n.start == selected["anchor"]["byte_start"])
    table = next(n for n in node.ancestors() if n.tag.endswith("}table"))
    row = next(n for n in node.ancestors() if n.tag.endswith("}tr"))

    def anchor(item):
        return {"source_sha256": digest(source), "source_version": filename,
                "byte_start": item.start, "byte_stop": item.stop,
                "span_sha256": digest(source[item.start:item.stop]), "dom_path": item.path}

    cells = [c for c in row.children if c.tag.rsplit("}", 1)[-1] in ("td", "th")]
    label = next(text(c) for c in cells if text(c))
    assert re.search(r"revenue", label, re.I), (filename, label)
    # A source-local structural title is sufficient for the literal reporting
    # question. It is deliberately not a cross-taxonomy equivalence assertion.
    titles = [n for n in nodes if n.stop <= table.start
              and n.start >= table.start - 8000
              and n.tag.rsplit("}", 1)[-1] in ("div", "p")
              and re.search(r"consolidated\s+state\s*ments?\s+of\s+(income|operations|earnings)", text(n), re.I)]
    assert titles, filename
    title = min(titles, key=lambda n: (table.start - n.stop, n.stop - n.start))
    prior_rows = [n for n in table.walk() if n.tag.endswith("}tr") and n.stop <= row.start]
    header_rows = [n for n in prior_rows if re.search(r"year\s+ended|20[0-9]{2}|million|thousand", text(n), re.I)]
    duplicates = [f for f in document["facts"] if eligible(f)
                  and f["reported_aspects"] == selected["reported_aspects"]]
    unique_values = sorted({f["normalized_value"] for f in duplicates}, key=Decimal)
    schema_refs = [n for n in nodes if n.tag.endswith("}schemaRef")]
    return {
        "filename": filename, "document_fiscal_year": metadata["actual_document_fiscal_year_values"][0],
        "filename_matches_document_fiscal_year": metadata["filename_matches_document_fiscal_year"],
        "source_sha256": digest(source), "source_bytes": len(source),
        "selection_provenance": "Agent source-reviewed primary-statement occurrence; replayed by disclosed ordinal, not inferred by a tested proposer.",
        "fact_ordinal": selected["fact_ordinal"], "qualified_concept": selected["concept"],
        "reported_aspects": selected["reported_aspects"], "normalized_value": selected["normalized_value"],
        "displayed_numeric_text": selected["lexical_text"], "scale": selected["attributes"].get("scale", "0"),
        "row_label": label, "fact_anchor": selected["anchor"],
        "binding_evidence_anchors": selected["evidence_anchors"],
        "row_anchor": anchor(row), "statement_table_anchor": anchor(table),
        "statement_title_anchor": anchor(title), "comparative_header_anchors": [anchor(n) for n in header_rows],
        "same_complete_binding_occurrences": [{"fact_ordinal": f["fact_ordinal"],
             "normalized_value": f["normalized_value"], "anchor": f["anchor"]} for f in duplicates],
        "same_complete_binding_unique_values": unique_values,
        "source_local_binding_unambiguous": len(unique_values) == 1,
        "schema_references": [{"href": next((v for k, v in n.attrs.items() if k.endswith("}href")), None),
                                "anchor": anchor(n)} for n in schema_refs],
        "external_dts_loaded": False, "taxonomy_equivalence_validated": False,
        "visual_rendering_checked": False,
        "source_review": "Full selected statement text, row label, comparative headings and reported typed dependencies inspected by the agent; no independent human adjudication.",
    }


def run(external, output):
    protocol_bytes = PROTOCOL.read_bytes()
    assert digest(protocol_bytes) == PROTOCOL_SHA256
    protocol = json.loads(protocol_bytes)
    records = []
    for issuer in protocol["history_order"]:
        metadata = protocol["histories"][issuer]
        sources = [inspect_source(m, external) for m in metadata]
        a, b = sources
        assert a["reported_aspects"]["period"] == b["reported_aspects"]["period"]
        assert a["reported_aspects"]["entity"] == b["reported_aspects"]["entity"]
        assert a["reported_aspects"]["unit"] == b["reported_aspects"]["unit"]
        assert all(s["source_local_binding_unambiguous"] for s in sources)
        difference = Decimal(b["normalized_value"]) - Decimal(a["normalized_value"])
        question = protocol["question_template"].format(earlier_document_fiscal_year=a["document_fiscal_year"])
        records.append({
            "probe_id": "cross-filing-v23-" + issuer, "history_id": issuer,
            "question": question + " Sources: " + a["filename"] + " and " + b["filename"] + ".",
            "question_provenance": "Protocol-template authored; six fixed history controls, not naturally collected user questions.",
            "sources": sources, "reference_provenance": protocol["reference_provenance"],
            "amounts_identical": difference == 0, "later_minus_earlier_reported_USD": str(difference),
            "answer_status": "resolved_literal_source_scoped_reporting_question",
            "baseline_status": "Agent-assisted typed and structural source audit resolves the question; no automatic retrieval or QA performance measurement.",
            "namespace_bridge": {"qualified_concepts_identical": a["qualified_concept"] == b["qualified_concept"],
                "semantic_equivalence": "unresolved_not_asserted",
                "source_scoped_reporting_correspondence": "admitted_for_this_literal_question_from_independently_inspected_primary_statement_labels_and_scopes",
                "stronger_change_explanation": "not_part_of_this_probe_and_not_admitted_from_amount_difference"},
            "residual_after_source_review": False, "held_out": False,
        })
    result = {
        "schema_version": "cross_filing_probe_v23", "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": PROTOCOL_SHA256, "script_sha256": digest(Path(__file__).read_bytes()),
        "reader_sha256": digest((ROOT / "src/temporal_state/typed_reader_v15_1.py").read_bytes()),
        "runtime": {"python": sys.version, "platform": platform.platform()},
        "replay_command": "python3 scripts/probe_cross_filing_v23.py --external-dir EXTERNAL --output NEW_RESULT.json",
        "counts": {"development_histories": len(records), "questions": len(records),
                   "resolved_reporting_questions": sum(not r["residual_after_source_review"] for r in records),
                   "same_amount_pairs": sum(r["amounts_identical"] for r in records),
                   "different_amount_pairs": sum(not r["amounts_identical"] for r in records),
                   "admitted_semantic_equivalence_bridges": 0, "qa_model_calls": 0,
                   "held_out_items": 0, "independent_human_gold_items": 0},
        "records": records,
        "conclusion": "All six literal reporting questions resolve under agent-assisted typed plus primary-statement structural review. This probe supplies development controls and two observed amount differences, not a retrieval advantage, a competent automatic reader result or an admitted explanation of change.",
    }
    with output.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "results/cross_filing_probe_v23.json")
    args = parser.parse_args()
    run(args.external_dir, args.output)

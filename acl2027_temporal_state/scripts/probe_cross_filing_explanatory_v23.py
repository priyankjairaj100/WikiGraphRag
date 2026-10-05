#!/usr/bin/env python3
"""Replay the separately frozen two-history source explanation audit.

Selected byte ranges and interpretations are disclosed agent annotations.
Reproduction checks these exact source ranges and numeric facts; it does not
constitute independent adjudication or automate semantic entailment.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from temporal_state.typed_reader_v15_1 import _parse, read_inline_xbrl

PROTOCOL = ROOT / "data/empirical_v23/cross_filing_explanatory_probe_protocol.json"
PROTOCOL_SHA256 = "7e737657f0a43a3165103fb097dcaab9defbba92b70af922e9d30bf5f9b0f47f"
FACTS = {
    "PFE_2023.html": {"product": 25, "alliance": 28, "total": 31, "other_net_income": 52, "pretax_continuing": 55},
    "PFE_2024.html": {"product": 26, "alliance": 29, "royalty": 32, "total": 35, "other_net_income": 56, "pretax_continuing": 59},
    "BRK-B_2022.html": {"service_and_other": 132, "total": 138, "pretax_before_equity": 188, "equity_method": 191, "pretax": 194},
    "BRK-B_2023.html": {"service_and_other": 127, "total": 133, "pretax_before_equity": 182, "equity_method": 185, "pretax": 188},
}
NARRATIVE = {
    "PFE_2024.html": {
        "royalty_row_footnote_to_note1a": (2129607, 2130534),
        "prior_period_presentation_intro": (2487234, 2487495),
        "royalty_reclassification": (2487495, 2489510),
        "rounding_caveat": (2491248, 2491563),
        "mda_independent_explicit_recast_statement": (1290950, 1292213),
    },
    "BRK-B_2023.html": {"generic_prior_year_reclassification": (4657346, 4658610)},
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(external, output):
    protocol_raw = PROTOCOL.read_bytes()
    assert digest(protocol_raw) == PROTOCOL_SHA256
    protocol = json.loads(protocol_raw)
    prior_path = ROOT / "results/cross_filing_probe_v23.json"
    assert digest(prior_path.read_bytes()) == protocol["prior_result_sha256"]
    prior = json.loads(prior_path.read_text())
    source_metadata = {s["filename"]: s for r in prior["records"] for s in r["sources"]}
    sources = {}
    for filename, selections in FACTS.items():
        source = (external / filename).read_bytes()
        assert digest(source) == source_metadata[filename]["source_sha256"]
        document = read_inline_xbrl(source, source_version=filename)
        nodes = _parse(source)
        by_start = {n.start: n for n in nodes}

        def anchor(start, stop):
            return {"source_version": filename, "source_sha256": digest(source),
                    "byte_start": start, "byte_stop": stop, "span_sha256": digest(source[start:stop])}

        facts = {}
        for name, ordinal in selections.items():
            fact = document["facts"][ordinal]
            assert fact["status"] == "normalized" and fact["binding_status"] == "reported_aspects_resolved"
            assert fact["reported_aspects"]["period"] == source_metadata[filename]["reported_aspects"]["period"]
            node = by_start[fact["anchor"]["byte_start"]]
            row = next(n for n in node.ancestors() if n.tag.endswith("}tr"))
            label = next(" ".join(n.text().split()) for n in row.children
                         if n.tag.endswith("}td") and " ".join(n.text().split()))
            facts[name] = {"fact_ordinal": ordinal, "reported_aspects": fact["reported_aspects"],
                           "normalized_value": fact["normalized_value"], "lexical_text": fact["lexical_text"],
                           "accuracy": fact["accuracy"], "row_label": label,
                           "anchor": fact["anchor"], "binding_evidence_anchors": fact["evidence_anchors"],
                           "row_anchor": anchor(row.start, row.stop)}
        narrative = {}
        for name, (start, stop) in NARRATIVE.get(filename, {}).items():
            assert by_start[start].stop == stop, (filename, name)
            narrative[name] = anchor(start, stop)
        sources[filename] = {"source_sha256": digest(source), "facts": facts, "narrative_anchors": narrative}

    def value(filename, name):
        return Decimal(sources[filename]["facts"][name]["normalized_value"])

    pfe_delta = value("PFE_2024.html", "total") - value("PFE_2023.html", "total")
    pfe_royalty = value("PFE_2024.html", "royalty")
    pfe_other_delta = value("PFE_2024.html", "other_net_income") - value("PFE_2023.html", "other_net_income")
    brk_delta = value("BRK-B_2023.html", "total") - value("BRK-B_2022.html", "total")
    brk_service_delta = value("BRK-B_2023.html", "service_and_other") - value("BRK-B_2022.html", "service_and_other")
    brk_equity_delta = value("BRK-B_2023.html", "equity_method") - value("BRK-B_2022.html", "equity_method")
    assert pfe_delta == -pfe_other_delta
    assert brk_delta == brk_service_delta == -brk_equity_delta
    assert value("BRK-B_2023.html", "pretax") == value("BRK-B_2022.html", "pretax")
    assert value("PFE_2024.html", "pretax_continuing") == value("PFE_2023.html", "pretax_continuing")

    # The external inspection files document the actual reviewed passage set.
    # They are regenerated by the disclosed searches, not bundled source text.
    review_files = ["cross_filing_explanatory_immediate_v23.json",
                    "cross_filing_explanatory_passages_v23.json",
                    "cross_filing_explanatory_followup_v23.json"]
    review_log = []
    inspection_inputs = {}
    for name in review_files:
        path = external / name
        inspection_inputs[name] = digest(path.read_bytes())
        for key, records in json.loads(path.read_text()).items():
            for record in records:
                filename = record.get("filename", key)
                source = (external / filename).read_bytes()
                start, stop = record["start"], record["stop"]
                item = {"inspection_file": name, "filename": filename,
                        "byte_start": start, "byte_stop": stop,
                        "span_sha256": digest(source[start:stop]),
                        "selection_reason": record.get("selection_reason", record.get("reason", record.get("kind")))}
                if "score" in record:
                    item["heuristic_search_score_not_coverage"] = record["score"]
                review_log.append(item)

    result = {
        "schema_version": "cross_filing_explanatory_probe_v23",
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": PROTOCOL_SHA256, "script_sha256": digest(Path(__file__).read_bytes()),
        "reader_sha256": digest((ROOT / "src/temporal_state/typed_reader_v15_1.py").read_bytes()),
        "runtime": {"python": sys.version, "platform": platform.platform()},
        "external_inspection_input_hashes": inspection_inputs,
        "source_records": sources, "reviewed_passage_locators": review_log,
        "provenance": "Protocol-authored questions; program-extracted numeric references and agent-authored semantic annotations. No independent human gold, model predictions or held-out evaluation.",
        "review_search_disclosure": {
            "primary": "Complete primary statements, immediately following footnotes and directly linked Note 1 presentation policy.",
            "supplementary": "Div/p blocks, length 40..4499 characters, containing reclass or conform; scored by occurrences of revenue(5), reclass(3), conform(3), presentation(2), prior(1), and royalty for PFE or equity method for BRK(6; other topic1). Text-identical nested blocks deduplicated to shortest byte range. Up to12 per history sorted score descending then filename/start.",
            "brk_remaining_two": "Only10 reclass/conform candidates existed. Two remaining passages selected from short nonnested blocks with equity method, prioritizing revenue(8), include(3), 2021(2), energy(2). They did not establish the cause.",
            "coverage_limit": "Ranking and search budget do not establish complete semantic coverage; a negative source-existence claim is not authorized.",
        },
        "records": [
            {"history_id": "PFE", "question": protocol["questions"]["PFE"],
             "outcome": "baseline_resolves_source_explanation",
             "cause_supported": "The later filing explicitly states that royalty income was moved from other income/deductions into total revenue and earlier periods were recast to the new presentation.",
             "causal_evidence": ["PFE_2024.html:prior_period_presentation_intro", "PFE_2024.html:royalty_reclassification", "PFE_2024.html:mda_independent_explicit_recast_statement"],
             "baseline_path": "Primary royalty row -> its Note1A footnote -> presentation-policy paragraph. The corroborating MD&A passage is unnecessary for the minimal baseline answer.",
             "reconciliation_USD": {"total_revenue_delta": str(pfe_delta), "displayed_royalty_component": str(pfe_royalty),
                "delta_minus_royalty": str(pfe_delta - pfe_royalty), "other_net_income_delta": str(pfe_other_delta),
                "pretax_continuing_income_delta": "0"},
             "precision_limit": "Displayed revenue increase is1,057 million; displayed royalty component is1,058 million. Retain the1 million residual. The filing's explicit rounding caveat makes this compatible with rounded presentation; exact unrounded attribution is not recovered.",
             "novel_method_advantage": False},
            {"history_id": "BRK-B", "question": protocol["questions"]["BRK-B"],
             "outcome": "partially_explained_residual",
             "cause_supported": "Only a generic statement that immaterial prior-year balances were reclassified to conform to the current presentation was found in the bounded review. It does not identify this109 million transfer or its reason.",
             "causal_evidence": ["BRK-B_2023.html:generic_prior_year_reclassification"],
             "reconciliation_USD": {"total_revenue_delta": str(brk_delta), "service_revenue_and_other_income_delta": str(brk_service_delta),
                "equity_method_earnings_delta": str(brk_equity_delta), "pretax_earnings_delta": "0"},
             "inference_not_authorized": "The numeric pattern is consistent with a reclassification between the service/other-income and equity-method lines, but arithmetic and generic boilerplate do not establish that exact causal bridge.",
             "required_answer_behavior": "Report the source-scoped amounts and numeric pattern; abstain from the specific causal explanation until an explicit source premise is acquired.",
             "coverage": "open: the bounded source review did not prove that no explanation exists elsewhere in the full pair",
             "novel_method_advantage": False}
        ],
        "counts": {"development_histories": 2, "baseline_resolved_explanations": 1,
                   "specific_cause_unresolved_after_bounded_review": 1, "qa_model_calls": 0,
                   "positive_retrieval_wins": 0, "held_out_items": 0},
        "reader_gate": "closed",
    }
    with output.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "results/cross_filing_explanatory_probe_v23.json")
    args = parser.parse_args()
    run(args.external_dir, args.output)

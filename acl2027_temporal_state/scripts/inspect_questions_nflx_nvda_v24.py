#!/usr/bin/env python3
"""Replay blind, model-assisted NFLX/NVDA source-question annotations.

These fixed spans were selected without viewing automatic retrieval outputs.
This script verifies byte provenance and records the disclosed bounded search;
it does not automate semantic adjudication or establish source-wide absence.
No third-party source text is emitted into the repository.
"""
import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from temporal_state.typed_reader_v15_1 import _parse

PROTOCOL_SHA256 = "214d0be8ba347c59a77a0a5b3c1f983d822ce9998999c7aae0ecfda654f21ae2"
FILES = ("NFLX_2022.html", "NFLX_2023.html", "NVDA_2022.html", "NVDA_2023.html")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def run(external, output):
    protocol_raw = (ROOT / "data/task_probe_v24/protocol.json").read_bytes()
    assert digest(protocol_raw) == PROTOCOL_SHA256
    protocol = json.loads(protocol_raw)
    expected = {s["external_filename"]: s for s in protocol["sources"]}
    raw = {fn: (external / fn).read_bytes() for fn in FILES}
    nodes = {}
    text = {}
    for fn in FILES:
        assert digest(raw[fn]) == expected[fn]["expected_sha256"]
        assert len(raw[fn]) == expected[fn]["expected_bytes"]
        parsed = _parse(raw[fn])
        nodes[fn] = {n.start: n for n in parsed}
        text[fn] = " ".join(parsed[0].text().split())

    def anchor(fn, start, stop=None):
        node = nodes[fn][start]
        if stop is not None:
            assert node.stop == stop, (fn, start, stop, node.stop)
        return {"source_file": fn, "byte_start": start, "byte_stop": node.stop,
                "span_sha256": digest(raw[fn][start:node.stop])}

    def anchors(fn, *starts):
        return [anchor(fn, s) for s in starts]

    def claim(key, statement, *witnesses, role="required"):
        return {"claim_id": key, "statement": statement, "role": role,
                "witness_sets": list(witnesses)}

    def reviewed(fn, *starts):
        return anchors(fn, *starts)

    search_terms = {
        "currency_growth": [r"constant.currency", r"operational.{0,20}(growth|revenue)",
                            r"revenues?.{0,40}(increas|flat)", r"foreign (currency|exchange)",
                            r"United States dollars"],
        "segment_recast": [r"operating segments?", r"reportable segments?", r"\brecast\w*",
                           r"\breclassif\w*", r"chief operating decision maker"],
        "free_cash_flow": [r"free cash flows?", r"non.GAAP", r"cash.{0,25}operating activities",
                           r"purchases of property", r"change in other assets"],
        "stock_compensation": [r"unrecognized.{0,40}compensation", r"unearned.{0,40}compensation",
                               r"weighted.average", r"nonvested", r"ESPP"],
    }
    records = []

    def record(issuer, family, status, answer, claims, spans, limitations):
        item = next(i for i in protocol["items"] if i["history_id"] == issuer and i["family"] == family)
        logs = []
        for fn in item["source_files"]:
            logs.append({"source_file": fn, "operation": "regex search over normalized complete XML text",
                         "term_counts": {p: len(re.findall(p, text[fn], re.I)) for p in search_terms[family]}})
        records.append({"item_id": item["item_id"], "history_id": issuer, "family": family,
                        "question": item["question"], "status": status, "answer_summary": answer,
                        "claims": claims, "review_scope": {
                            "source_files": item["source_files"], "reviewed_passage_anchors": spans,
                            "method": "Short span/paragraph and table-row keyword inspection, followed by direct inspection of the relevant MD&A, segment, cash-flow or share-compensation note and local headers; no automatic retrieval ranking inspected.",
                            "coverage": "bounded model-assisted source review; not an independent external coverage certificate"},
                        "search_log": logs, "limitations": limitations})

    n = "NFLX_2023.html"
    # Direct reported growth; the aggregate CC percentage was not explicitly found.
    record("NFLX", "currency_growth", "partial",
           "NFLX reports 7% consolidated revenue growth for the year ended December 31, 2023 versus 2022. It discloses an approximately USD597 million revenue uplift if the prior year's corresponding monthly exchange rates were held fixed. That is a constant-currency counterfactual, not organic/operational growth. The regional constant-currency percentages shown in the tables describe average monthly revenue per paying membership, not aggregate revenue growth. No separately stated consolidated constant-currency growth percentage was found in this bounded review. Using the disclosed total revenues and approximate currency uplift gives derived (not company-reported) growth of about 8.56%, versus 6.67% from displayed GAAP amounts, an approximately 1.89 percentage-point difference.",
           [claim("nflx_reported_growth", "Consolidated revenue for calendar FY2023 grew 7% versus FY2022, as reported.", anchors(n,366476)),
            claim("nflx_total_revenues", "Consolidated total revenues were USD33,723,297 thousand in 2023 and USD31,615,550 thousand in 2022.", anchors(n,338857,339447,340967,347101)),
            claim("nflx_cc_definition_and_uplift", "Holding foreign exchange rates at the corresponding prior-year monthly rates raises the 2023 revenue counterfactual by approximately USD597 million; the disclosed non-GAAP currency measure is used to analyze average monthly revenue per paying membership.", anchors(n,462805)),
            claim("nflx_cc_uplift_alternative", "For 2023, the filing's market-risk discussion independently reports approximately USD597 million higher revenues at the 2022-period exchange rates.", anchors(n,616487), role="corroborating")],
           reviewed(n,366476,338857,339447,340967,347101,378451,462805,616487),
           ["The 8.56%, 6.67% and 1.89-point quantities are disclosed-input arithmetic, not reported growth percentages; the USD597 million input is approximate.",
            "A separate company-reported aggregate constant-currency growth percentage remains unlocated; regional ARPU changes must not be substituted for it."])

    record("NFLX", "segment_recast", "partial",
           "Both annual reports describe one operating segment, with the co-chief executive officers reviewing consolidated financial information. Thus the observed one-segment decision basis is the same in both source statements. The 2023 filing says it is evaluating ASU2023-07; that statement is not evidence of an already completed segment recast. No explicit comparative-period segment recast statement was located in the bounded review, so a stronger claim that no presentation changes occurred is not authorized.",
           [claim("nflx_segment_2022", "The 2022 annual report describes one operating segment and CODM review on a consolidated basis.", anchors("NFLX_2022.html",1804510)),
            claim("nflx_segment_2023", "The 2023 annual report describes one operating segment and CODM review on a consolidated basis.", anchors(n,1625991)),
            claim("nflx_segment_asu", "The 2023 filing describes ASU2023-07 as under evaluation, with its stated effective fiscal years beginning after December15,2023.", anchors(n,956023))],
           reviewed("NFLX_2022.html",171415,1804510)+reviewed(n,171813,956023,1625991),
           ["Positive source statements establish the one-segment/consolidated review basis. Bounded keyword and segment-note inspection does not prove the absence of every recast or presentation change."])

    hdr = [572782,573526,574968]
    cf_hdr = [741784,742691,743333,745471]
    record("NFLX", "free_cash_flow", "supported",
           "For FY2023, NFLX defines non-GAAP free cash flow as operating cash flow less purchases of property/equipment and the change in other assets. Its reconciliation is USD7,274,301 thousand minus USD348,552 thousand minus zero = USD6,925,749 thousand. The displayed thousand-dollar values reconcile exactly; that does not recover unrounded transaction values.",
           [claim("nflx_fcf_definition", "The issuer-defined non-GAAP measure deducts property/equipment purchases and the change in other assets from cash provided by operating activities.", anchors(n,570172)),
            claim("nflx_cfo", "FY2023 operating cash flow is USD7,274,301 thousand.", anchors(n,*hdr,575453), anchors(n,*hdr,585111), anchors(n,*cf_hdr,776791)),
            claim("nflx_ppe", "FY2023 purchases of property and equipment are a USD348,552 thousand deduction.", anchors(n,*hdr,575453,587552), anchors(n,*cf_hdr,779857)),
            claim("nflx_other_assets", "FY2023's FCF reconciliation shows a zero adjustment for the change in other assets (dash convention).", anchors(n,*hdr,589931)),
            claim("nflx_fcf_value", "FY2023 free cash flow is USD6,925,749 thousand.", anchors(n,*hdr,592346)),
            claim("nflx_fcf_arithmetic", "At displayed thousand-dollar precision, 7,274,301 −348,552 −0 =6,925,749 with no rounding residual.", anchors(n,*hdr,575453,587552,589931,592346))],
           reviewed(n,570172,*hdr,575453,585111,587552,589931,592346,*cf_hdr,776791,779857),
           ["Exactness is limited to displayed precision, not unrounded source accounting data."])

    record("NFLX", "stock_compensation", "supported",
           "At December31,2023, NFLX reports USD26 million of unrecognized compensation cost related to nonvested stock options, expected over a weighted-average 0.45 years. This is the nonvested-option amount and period; no broader all-award aggregate is inferred.",
           [claim("nflx_nonvested_options", "At December31,2023, unrecognized compensation for nonvested stock options is USD26 million with a weighted-average remaining recognition period of0.45 years.", anchors(n,1458222))],
           reviewed(n,1434083,1457291,1458222),
           ["The amount is specifically for nonvested options; it must not be converted into an aggregate for any undisclosed award population."])

    v = "NVDA_2023.html"
    record("NVDA", "currency_growth", "partial",
           "NVDA describes FY2023 revenue of approximately USD26.97 billion as flat against FY2022. The segment revenue table reports USD26,974 million versus USD26,914 million and a dash at whole-percentage growth precision. The filing says its sales and third-party manufacturing arrangements are priced and paid in US dollars. A constant-currency or operational revenue growth measure was not located; the USD pricing statement does not authorize setting a separate currency-growth effect to zero.",
           [claim("nvda_reported_growth", "FY2023 revenue was approximately USD26.97 billion, described by the issuer as flat year over year.", anchors(v,546030)),
            claim("nvda_total_revenues", "Revenue totals for years ended January29,2023 and January30,2022 are USD26,974 million and USD26,914 million, with displayed difference USD60 million and dash percentage.", anchors(v,594154,594422,596172,601010)),
            claim("nvda_usd_pricing", "The filing describes sales and third-party manufacturing arrangements as providing for US-dollar pricing and payment.", anchors(v,689189))],
           reviewed(v,546030,594154,594422,596172,601010,688534,689189,690175),
           ["No company-disclosed constant-currency or operational growth value was located in the bounded review. No global source-absence claim or zero-currency-effect inference is made."])

    record("NVDA", "segment_recast", "partial",
           "Both reports identify two operating segments, Graphics and Compute & Networking, and describe an All Other category of unallocated enterprise expenses. The fiscal2023 report updates the component descriptions and lists additional categories of All Other costs, but that wording alone does not establish a transfer or recast. Both accounting-policy notes state only that some prior fiscal-year balances were reclassified to the current presentation. Neither generic statement identifies a segment-specific comparative recast in the reviewed passages, so that relation remains unresolved.",
           [claim("nvda_segments_2022", "The FY2022 report identifies two segments: Graphics and Compute & Networking.", anchors("NVDA_2022.html",485334), anchors("NVDA_2022.html",2215420,2216006,2216559)),
            claim("nvda_segments_2023", "The FY2023 report identifies two segments: Compute & Networking and Graphics.", anchors(v,521618), anchors(v,2349209,2349732,2350342)),
            claim("nvda_components_2022", "The earlier Graphics description covers GeForce/GeForceNOW, workstation/vGPU and automotive infotainment/Omniverse; Compute & Networking covers Data Center, Mellanox networking, automotive AI, CMP, Jetson and enterprise software.", anchors("NVDA_2022.html",2215420,2216006)),
            claim("nvda_components_2023", "The later Graphics description retains those broad areas with updated RTX/Omniverse wording; Compute & Networking includes Data Center, networking, automotive AI and electric-vehicle platforms, Jetson, enterprise software and CMP. This is a description comparison, not an equivalence or causal-reclassification finding.", anchors(v,2349209,2349732)),
            claim("nvda_all_other", "Both reports identify All Other as enterprise costs not assigned by the CODM to the two segments; the lists of disclosed costs differ.", anchors("NVDA_2022.html",2217465)+anchors(v,2351238)),
            claim("nvda_generic_reclassification", "Both reports say some prior fiscal-year balances were reclassified to conform to current presentation, without identifying a segment-specific recast in those statements.", anchors("NVDA_2022.html",1182926)+anchors(v,1246023))],
           reviewed("NVDA_2022.html",485334,1182926,2214997,2215420,2216006,2216559,2217465)+reviewed(v,521618,1246023,2348796,2349209,2349732,2350342,2351238,2351963),
           ["Shared segment names do not prove all underlying boundaries are invariant. Generic reclassification boilerplate does not establish which segment amounts were recast or why."])

    record("NVDA", "free_cash_flow", "unresolved",
           "An issuer-defined free-cash-flow measure and reconciliation were not located in the two reviewed annual reports. The FY2023 MD&A reports operating cash flow, but this alone is insufficient to define the company's free cash flow. No free-cash-flow value is constructed from CFO and capital expenditures.",
           [], reviewed(v,658801,659234,660115,660569),
           ["Full decoded-text searches for free cash flow returned zero matches in both reports. This is a search result, not a semantic proof of nonexistence or a statement about earnings releases outside the frozen corpus.",
            "The question is retained unresolved; it is not treated as an automatic method failure, globally insufficient example, or zero-valued FCF observation."])

    record("NVDA", "stock_compensation", "supported",
           "At January29,2023, NVDA reports USD6.56 billion of aggregate unearned stock-based compensation expense. The expected weighted-average recognition period is2.6 years for RSUs, PSUs and market-based PSUs, and1.0 year for ESPP. The source does not apportion the aggregate dollar amount between those groups; no common aggregate recognition period is inferred.",
           [claim("nvda_unearned_sbc", "At January29,2023, aggregate unearned stock-based compensation expense is USD6.56 billion.", anchors(v,1458698)),
            claim("nvda_periods_by_award", "Expected weighted-average recognition periods are2.6 years for RSUs/PSUs/market-based PSUs and1.0 year for ESPP, on the stated January29,2023 aggregate unearned-compensation disclosure.", anchors(v,1458698))],
           reviewed(v,1458698),
           ["Separate award-group periods must be preserved; the source's aggregate dollar amount is not silently split, and no aggregate recognition period is invented."])

    # Disclosed-input arithmetic checks; no model prediction or semantic test.
    assert Decimal("7274301")-Decimal("348552") == Decimal("6925749")
    arithmetic = {"nflx_fcf_residual_USD_thousands": "0",
                  "nflx_growth_from_displayed_amounts_pct": str((Decimal("33723297")/Decimal("31615550")-1)*100),
                  "nflx_approx_cc_growth_derived_pct": str(((Decimal("33723297")+Decimal("597000"))/Decimal("31615550")-1)*100),
                  "nflx_approx_cc_difference_derived_pp": str(Decimal("597000")/Decimal("31615550")*100)}
    result = {"schema": "source_question_review_v24", "protocol_sha256": PROTOCOL_SHA256,
              "script_sha256": digest(Path(__file__).read_bytes()),
              "sources": [{"filename": fn, "sha256": digest(raw[fn])} for fn in FILES],
              "records": records, "counts": dict(Counter(r["status"] for r in records)),
              "arithmetic_checks": arithmetic, "model_assisted_review": True, "human_gold": False,
              "automatic_retrieval_outputs_viewed": False, "large_model_predictions": 0,
              "held_out_items": 0,
              "limitations": ["All eight prospectively templated development questions are retained.",
                              "Witness alternatives are observed, not exhaustive. A second source-only review and retrieved-alternative adjudication remain separate steps.",
                              "Partial/unresolved records do not constitute a benchmark of source insufficiency."]}
    with output.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps({"records": len(records), "counts": result["counts"]}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "results/source_questions_nflx_nvda_v24.json")
    args = parser.parse_args()
    run(args.external_dir, args.output)

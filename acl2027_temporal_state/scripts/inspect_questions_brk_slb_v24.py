#!/usr/bin/env python3
"""Reproduce bounded, assistant-authored BRK/SLB v24 source annotations.

No retrieval results are inputs. Exact source bytes remain outside the repository.
This script checks source/anchor integrity; it does not independently establish
semantic entailment or prove a disclosure absent from a filing.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from temporal_state.typed_reader_v15_1 import _parse

PROTOCOL_HASH = "214d0be8ba347c59a77a0a5b3c1f983d822ce9998999c7aae0ecfda654f21ae2"
FILES = ["BRK-B_2022.html", "BRK-B_2023.html", "SLB_2022.html", "SLB_2023.html"]
PATTERNS = {
    "currency_growth": [r"constant[ -]currency", r"currency[ -]neutral", r"operational growth", r"organic", r"revenue.{0,30}(growth|grew|increas)", r"foreign currency", r"exchange rate"],
    "segment_recast": [r"reportable.{0,20}segment", r"operating segments", r"segment information", r"business segment data", r"four Divisions", r"\brecast\w*", r"reclassif\w*", r"prior.year.{0,40}present"],
    "free_cash_flow": [r"free[ -]cash[ -]flow", r"cash flow.{0,20}operat", r"net operating cash", r"capital expenditures", r"liquidity"],
    "stock_compensation": [r"unrecognized", r"stock[ -]based compensation", r"share[ -]based compensation", r"weighted[ -]average.{0,40}(period|year)", r"nonvested", r"unvested", r"recognition period"],
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(external):
    protocol_bytes = (ROOT / "data/task_probe_v24/protocol.json").read_bytes()
    assert sha(protocol_bytes) == PROTOCOL_HASH
    protocol = json.loads(protocol_bytes)
    metadata = {s["external_filename"]: s for s in protocol["sources"]}
    sources, nodes, visible_text, paragraph_counts = {}, {}, {}, {}
    for filename in FILES:
        data = (external / filename).read_bytes()
        assert sha(data) == metadata[filename]["expected_sha256"]
        assert len(data) == metadata[filename]["expected_bytes"]
        parsed = _parse(data)
        sources[filename] = data
        nodes[filename] = {n.start: n for n in parsed}

        def visible(node):
            if node.tag.endswith(("}header", "}hidden")):
                return ""
            return " ".join(visible(part) if hasattr(part, "tag") else part
                            for part in node.parts)

        visible_text[filename] = " ".join(visible(parsed[0]).split())
        paragraph_counts[filename] = sum(n.tag.endswith(("}p", "}tr")) for n in parsed)

    def a(filename, start):
        node = nodes[filename][start]
        return {"source_file": filename, "byte_start": start,
                "byte_stop": node.stop, "span_sha256": sha(sources[filename][start:node.stop])}

    def aa(filename, starts):
        return [a(filename, start) for start in starts]

    def claim(cid, statement, witnesses, role="required"):
        return {"claim_id": cid, "statement": statement, "role": role,
                "witness_sets": witnesses}

    records = []
    items = {(i["history_id"], i["family"]): i for i in protocol["items"]}

    def add(history, family, status, summary, claims, reviewed, limitations):
        item = items[history, family]
        log = []
        for fn in item["source_files"]:
            log.append({"source_file": fn,
                        "search_surface": "whitespace-normalized XML text with ix:header/hidden subtrees excluded; counts are lexical occurrences, not evidence counts",
                        "patterns": [{"regex": p, "occurrences": len(re.findall(p, visible_text[fn], re.I))}
                                     for p in PATTERNS[family]],
                        "reviewed_spans": aa(fn, reviewed.get(fn, []))})
        records.append({"item_id": item["item_id"], "history_id": history,
                        "family": family, "question": item["question"],
                        "status": status, "answer_summary": summary, "claims": claims,
                        "review_scope": "Bounded source-only assistant inspection of the two frozen annual-report files, the listed lexical searches and relevant notes/MD&A/table neighborhoods. Source identity and actual fiscal year come from the shared immutable inventory. Not a full financial or taxonomy audit; search failure does not establish global insufficiency.",
                        "search_log": log, "limitations": limitations})

    brk_old, brk = FILES[:2]
    slb_old, slb = FILES[2:]

    # Use the comparative amounts in the same FY2022 statement, preserving its basis.
    brk_revenue = aa(brk, [3750462, 3750819, 3751169, 3751773, 3753108,
                           3754933, 3830532, 3831862, 3833891])
    add("BRK-B", "currency_growth", "partial",
        "The FY2022 consolidated statement reports revenue of $302,089 million and comparative FY2021 revenue of $276,203 million. Their difference is $25,886 million, approximately 9.3721% of the displayed FY2021 amount; that percentage is reviewer-calculated, not a quoted issuer growth rate. A consolidated constant-currency/operational growth disclosure was not located in this bounded review. No currency difference is authorized.",
        [claim("brk_revenue_pair", "Consolidated total revenue is $302,089 million for 2022 and $276,203 million for 2021 on the comparative basis of the FY2022 statement.", [brk_revenue]),
         claim("brk_revenue_derived_growth", "Using the displayed rounded totals, 302089 - 276203 = 25886 million dollars and 25886 / 276203 * 100 = approximately 9.3721%; this is a calculation, not an issuer-disclosed constant-currency or operational rate.", [brk_revenue], "derived")],
        {brk: [3830387, 1905940, 1987373, 2770345, 2778179]},
        ["Currency effects discussed for individual businesses or earnings are not a consolidated constant-currency revenue growth series.",
         "No assertion that the company never disclosed such a measure outside the reviewed files, or that a source-supported answer does not exist elsewhere.",
         "The file labeled BRK-B_2023.html is the fiscal 2022 annual report; its comparative 2021 amount is used without silently substituting the earlier filing's differently presented amount."])

    old_segment = [10893193, 10895643, 10896893, 10898190, 10899464, 10900756, 10902136, 10903449, 10904696]
    new_segment = [11086994, 11091193, 11093976, 11096806, 11099613, 11102610, 11105527, 11108543, 11111327]
    add("BRK-B", "segment_recast", "partial",
        "Both fiscal 2021 and fiscal 2022 Note 25 list the same eight operating segments: GEICO, Berkshire Hathaway Primary Group, Berkshire Hathaway Reinsurance Group, BNSF, BHE, Manufacturing, McLane, and Service and retailing. The later basis-of-consolidation note says certain immaterial prior-year financial-statement balances were reclassified to the current presentation. It does not identify a reportable-segment recast. A specific segment-recast relation remains unresolved; equal segment names do not establish identical accounting populations.",
        [claim("brk_earlier_segments", "The FY2021 report identifies GEICO, Berkshire Hathaway Primary Group, Berkshire Hathaway Reinsurance Group, Railroad/BNSF, Utilities and energy/BHE, Manufacturing, McLane Company, and Service and retailing as operating segments.", [aa(brk_old, old_segment)]),
         claim("brk_later_segments", "The FY2022 report lists the same eight named operating segments, with the corresponding business descriptions.", [aa(brk, new_segment)]),
         claim("brk_generic_reclassification", "The FY2022 basis-of-consolidation note discloses reclassification of certain immaterial prior-year financial-statement balances to conform to current presentation, without specifying a segment-level recast.", [aa(brk, [4657346])])],
        {brk_old: [10891558, 10892245] + old_segment,
         brk: [4657346, 11084764, 11085748] + new_segment},
        ["Matching named segments is an observed presentation comparison, not proof that every segment's contents or numbers are unchanged.",
         "No causal link is inferred from any equal-dollar revenue/equity-income movement.",
         "The generic prior-year reclassification statement cannot authorize a specific segment-recast answer."])

    add("BRK-B", "free_cash_flow", "unresolved",
        "No issuer-defined free-cash-flow measure or reconciliation was located. The report supplies operating cash flow and capital expenditure information, but subtracting those would introduce a reviewer-selected definition, so no FCF amount is given.", [],
        {brk_old: [3163549], brk: [3095684, 4458028]},
        ["The bounded review is not proof of absence throughout all Berkshire publications.",
         "No computed operating-cash-minus-capex quantity is relabeled as issuer-defined free cash flow."])

    add("BRK-B", "stock_compensation", "unresolved",
        "An unrecognized stock-based compensation balance and weighted-average recognition period were not located in the two frozen reports. Unrecognized tax benefits are a different disclosure and are not substituted.", [],
        {brk: [9284783, 9704705]},
        ["No zero balance, lack of stock awards, or globally unanswerable question is inferred from the unsuccessful source search."])

    add("SLB", "currency_growth", "partial",
        "SLB states that full-year 2023 revenue was $33.1 billion and increased 18% year on year. A constant-currency/operational revenue-growth counterpart was not located. Revenue growth, geographic growth, and currency effects on expenses or net assets are not interchangeable; no currency difference is calculated.",
        [claim("slb_reported_revenue_growth", "SLB reports 18% revenue growth for full-year 2023 versus 2022.", [aa(slb, [819497]), aa(slb, [668295])]),
         claim("slb_reported_revenue_level", "Full-year 2023 revenue is reported in the MD&A as approximately $33.1 billion.", [aa(slb, [819497])])],
        {slb: [668295, 819497, 1250915, 2648867]},
        ["No consolidated constant-currency revenue growth series is established by the disclosure that 72% of revenue was US-dollar denominated.",
         "The missing counterpart is unresolved after bounded review, not certified absent."])

    slb_old_segments = [4434012, 4435286, 4437011, 4438720, 4440535]
    slb_new_segments = [3267898, 3268520, 3269342, 3270152, 3270989]
    add("SLB", "segment_recast", "partial",
        "Both the 2022 and 2023 segment notes describe the same four divisions as segments: Digital & Integration, Reservoir Performance, Well Construction, and Production Systems. Their stated business descriptions match. No explicit comparative-period segment-recast statement was located in the reviewed notes and searches; this does not certify that all underlying segment populations or accounting allocations stayed identical.",
        [claim("slb_earlier_segments", "SLB's fiscal 2022 segment basis comprises Digital & Integration, Reservoir Performance, Well Construction, and Production Systems, with the listed functional descriptions.", [aa(slb_old, slb_old_segments)]),
         claim("slb_later_segments", "SLB's fiscal 2023 segment basis contains the same four divisions and matching functional descriptions.", [aa(slb, slb_new_segments)])],
        {slb_old: [4433200, 4433467] + slb_old_segments,
         slb: [3266704, 3267249] + slb_new_segments},
        ["A negative result from recast/reclassification searches is not a completeness certificate.",
         "This question does not establish a changing reportable-segment presentation or a need for a novel retrieval mechanism."])

    # Visible unit/period headers plus the first dollar sign establish table scope.
    common = [950216, 968401, 970667]
    fc_rows = {"operations": [990496, 991127],
               "capex": [992901, 993442, 993731],
               "aps": [995113, 995649, 995936],
               "exploration": [997316, 997933, 998256],
               "fcf": [999739, 1000525]}
    fcf_claims = [claim("slb_fcf_definition",
                       "SLB defines its non-GAAP free cash flow as operating cash flow minus capital expenditures, APS investments, and capitalized exploration-data costs; it does not describe residual cash available for discretionary expenditure.",
                       [aa(slb, [1043108])])]
    for key, statement in [
        ("operations", "SLB's 2023 cash flow from operations is $6,637 million."),
        ("capex", "The 2023 free-cash-flow bridge deducts capital expenditures of $1,939 million."),
        ("aps", "The 2023 free-cash-flow bridge deducts APS investments of $507 million."),
        ("exploration", "The 2023 free-cash-flow bridge deducts capitalized exploration-data costs of $153 million."),
        ("fcf", "SLB reports 2023 free cash flow of $4,038 million.")]:
        fcf_claims.append(claim("slb_fcf_" + key, statement, [aa(slb, common + fc_rows[key])]))
    fcf_claims.append(claim("slb_fcf_reconciliation",
                           "At the table's displayed million-dollar precision, 6637 - 1939 - 507 - 153 = 4038 with zero arithmetic residual. This checks displayed amounts, not undisclosed unrounded cash flows.",
                           [aa(slb, [1043108] + common + sum(fc_rows.values(), []))], "derived"))
    add("SLB", "free_cash_flow", "supported",
        "SLB's issuer-defined 2023 FCF is $4,038 million: operating cash flow $6,637 million less capital expenditures $1,939 million, APS investments $507 million, and capitalized exploration-data costs $153 million. The displayed million-dollar bridge sums exactly, with zero displayed residual; exact unrounded reconciliation is not established. FCF is a non-GAAP liquidity measure and is not residual cash available for discretionary spending.",
        fcf_claims,
        {slb_old: [1286823], slb: [668295, 949078, 968401, 990363, 992768, 994980, 997183, 999606, 1043108]},
        ["The narrative's rounded $6.6 billion operating cash flow and $4.0 billion FCF do not replace the exact displayed million-dollar table amounts.",
         "This is source-supported definition/reconciliation work. It is not evidence of a retrieval win over a footnote/structural baseline."])

    add("SLB", "stock_compensation", "partial",
        "At December 31, 2023, SLB reports $278 million of total unrecognized cost for nonvested stock-based compensation arrangements. The disclosed recognition schedule is $164 million in 2024, $89 million in 2025, $21 million in 2026, and $4 million in 2027. A weighted-average recognition period is not supplied in the reviewed passage or located elsewhere by the bounded searches, so none is invented. The balance is a disclosed total, not separate balances by award type.",
        [claim("slb_unrecognized_sbc", "At December 31, 2023, total unrecognized compensation cost related to nonvested stock-based compensation arrangements is $278 million.", [aa(slb, [3026862])]),
         claim("slb_sbc_schedule", "The disclosed future recognition schedule is $164 million in 2024, $89 million in 2025, $21 million in 2026, and $4 million in 2027.", [aa(slb, [3026862])]),
         claim("slb_sbc_program_scope", "The stock-based compensation note describes restricted stock units/performance share units, a discounted stock purchase plan, and stock options; its unrecognized-cost statement reports an aggregate rather than an award-type split.", [aa(slb, [2788631, 3026862])])],
        {slb_old: [4157357], slb: [2788631, 3002971, 3026862, 3030468]},
        ["An annual schedule does not fix intra-year timing and cannot be converted to an issuer-reported weighted-average period.",
         "The failure to locate a period is not certified corpus-wide insufficiency.",
         "A typed-reader failure on these tagged values would be a parser/coverage limitation, not a novel task."])

    return {"schema": "source_questions_brk_slb_v24", "protocol_sha256": PROTOCOL_HASH,
            "model_assisted_review": True, "human_gold": False,
            "blind_to_automatic_retrieval_outcomes": True,
            "sources": [{"filename": fn, "sha256": sha(sources[fn]),
                         "actual_fiscal_year": metadata[fn]["actual_document_fiscal_year_values"][0]}
                        for fn in FILES],
            "records": records,
            "summary": {"records": len(records), "supported": sum(r["status"] == "supported" for r in records),
                        "partial": sum(r["status"] == "partial" for r in records),
                        "unresolved": sum(r["status"] == "unresolved" for r in records),
                        "not_applicable": sum(r["status"] == "not_applicable" for r in records)},
            "limitations": ["Prospectively authored template questions on development sources; no naturally collected question claim.",
                            "No large-model predictions, held-out evaluation, independent human adjudication, or QA-performance measurement.",
                            "Observed witness sets are not an exhaustive enumeration of semantically sufficient alternatives.",
                            "Byte/hash validity does not prove semantic entailment; independent role review is still required."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--external", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "results/source_questions_brk_slb_v24.json")
    args = parser.parse_args()
    result = run(args.external)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result["summary"]))

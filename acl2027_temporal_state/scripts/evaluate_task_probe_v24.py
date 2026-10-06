#!/usr/bin/env python3
"""Exact source-witness retrieval diagnostic; not semantic coverage or QA scoring."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def merged(intervals):
    result = []
    for a, b in sorted(intervals):
        if type(a) is not int or type(b) is not int or a < 0 or b <= a:
            raise ValueError("invalid byte interval")
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def covered(anchor, ranges):
    start, stop = anchor["byte_start"], anchor["byte_stop"]
    return any(a <= start and stop <= b
               for a, b in merged(ranges.get(anchor["source_file"], [])))


def validate_selection(row, catalog, expected_sources):
    ids = row["selected_block_ids"]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate selected block")
    ranges, cost = {}, 0
    for block_id in ids:
        block = catalog[block_id]
        source = block["source_version"]
        if block["source_sha256"] != expected_sources[source]:
            raise ValueError("catalog source identity mismatch")
        if type(block["render_words"]) is not int or block["render_words"] < 0:
            raise ValueError("invalid block render cost")
        cost += block["render_words"]
        ranges.setdefault(source, []).extend(block["rendered_source_intervals"])
    if cost != row["rendered_words"]:
        raise ValueError("record cost differs from selected blocks")
    if {s: merged(v) for s, v in ranges.items()} != row["rendered_source_intervals"]:
        raise ValueError("record source coverage differs from selected blocks")


def evaluate(protocol, references, retrieval, expected_policies=None, expected_budgets=None):
    scheduled = {i["item_id"]: i for i in protocol["items"]}
    refs = {r["item_id"]: r for r in references}
    if len(refs) != len(references) or set(refs) != set(scheduled):
        raise ValueError("references must retain every scheduled item exactly once")
    seen = set()
    results = []
    summaries = {}
    for row in retrieval["records"]:
        item_id = row["item_id"]
        key = (item_id, row["policy"], row["budget_words"])
        if key in seen or item_id not in scheduled:
            raise ValueError("duplicate or unscheduled retrieval record")
        seen.add(key)
        ref = refs[item_id]
        if (type(row["rendered_words"]) is not int or row["rendered_words"] < 0
                or type(row["budget_words"]) is not int or row["budget_words"] <= 0
                or row["rendered_words"] > row["budget_words"]):
            raise ValueError("retrieval exceeded the declared rendered-word budget")
        claim_hits, missing = [], []
        for claim in ref["claims"]:
            witnesses = claim["witness_sets"]
            if not witnesses or any(not w for w in witnesses):
                raise ValueError("a claimed source-supported proposition needs a nonempty witness")
            hit = any(all(covered(a, row["rendered_source_intervals"]) for a in w)
                      for w in witnesses)
            (claim_hits if hit else missing).append(claim["claim_id"])
        complete = ref["status"] == "supported" and bool(ref["claims"])
        all_hits = bool(ref["claims"]) and not missing
        group = (row["policy"], row["budget_words"])
        counter = summaries.setdefault(group, Counter())
        counter["scheduled_questions"] += 1
        counter["source_supported_complete_questions"] += complete
        counter["complete_questions_all_recorded_witnesses_retrieved"] += complete and all_hits
        counter["all_annotated_claims"] += len(ref["claims"])
        counter["annotated_claims_with_a_retrieved_witness_set"] += len(claim_hits)
        counter["reference_" + ref["status"]] += 1
        results.append({"item_id": item_id, "history_id": ref["history_id"],
                        "family": ref["family"], "policy": row["policy"],
                        "budget_words": row["budget_words"],
                        "rendered_words": row["rendered_words"],
                        "reference_status": ref["status"],
                        "covered_claim_ids": claim_hits, "missing_recorded_claim_ids": missing,
                        "all_recorded_witnesses_retrieved": all_hits,
                        "source_supported_complete_question": complete,
                        "semantic_residual": "not_determined_requires_alternative_evidence_review"})
    for policy, budget in summaries:
        if {k[0] for k in seen if k[1:] == (policy, budget)} != set(scheduled):
            raise ValueError("a policy/budget omitted scheduled questions")
    if expected_policies is not None and expected_budgets is not None:
        expected = {(i, p, b) for i in scheduled for p in expected_policies for b in expected_budgets}
        if seen != expected:
            raise ValueError("retrieval roster differs from the frozen full factorial schedule")
    return {"schema": "task_probe_witness_diagnostic_v24",
            "scope": "mechanical exact-anchor recall against assistant-authored development references",
            "summaries": [{"policy": p, "budget_words": b, **dict(c)}
                          for (p, b), c in sorted(summaries.items())],
            "records": results,
            "semantic_premise_recall": None,
            "natural_qa_accuracy": None, "model_predictions": 0,
            "automatic_coverage_authorized": False,
            "limitations": [
                "Missing a recorded source span does not imply missing its semantic premise.",
                "Alternative evidence, including typed cards with equivalent scope, needs separate review.",
                "Reference status is assistant source review, not independent human adjudication.",
                "Partial and unresolved questions remain in the scheduled denominator.",
                "Words are an engineering budget, not token counts or complete acquisition cost."]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--protocol", type=Path, default=ROOT / "data/task_probe_v24/protocol.json")
    p.add_argument("--references", type=Path, nargs="+", required=True)
    p.add_argument("--retrieval", type=Path, required=True)
    p.add_argument("--retrieval-freeze", type=Path,
                   default=ROOT / "data/task_probe_v24/retrieval_freeze.json")
    p.add_argument("--source-dir", type=Path, required=True)
    p.add_argument("--pack-dir", type=Path,
                   help="optional external rendered packs for exact hash and word-count validation")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    protocol = json.loads(args.protocol.read_text())
    source_bytes = {}
    for s in protocol["sources"]:
        data = (args.source_dir / s["external_filename"]).read_bytes()
        if len(data) != s["expected_bytes"] or hashlib.sha256(data).hexdigest() != s["expected_sha256"]:
            raise ValueError("source identity mismatch")
        source_bytes[s["external_filename"]] = data
    refs = []
    for path in args.references:
        data = json.loads(path.read_text())
        if data["protocol_sha256"] != sha(args.protocol):
            raise ValueError("reference protocol mismatch")
        for row in data["records"]:
            for claim in row["claims"]:
                for witness in claim["witness_sets"]:
                    for anchor in witness:
                        body = source_bytes[anchor["source_file"]]
                        a, b = anchor["byte_start"], anchor["byte_stop"]
                        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(body):
                            raise ValueError("reference source bounds invalid")
                        if hashlib.sha256(body[a:b]).hexdigest() != anchor["span_sha256"]:
                            raise ValueError("reference span identity mismatch")
            refs.append(row)
    retrieval = json.loads(args.retrieval.read_text())
    freeze = json.loads(args.retrieval_freeze.read_text())
    if (retrieval["protocol_sha256"] != sha(args.protocol)
            or freeze["protocol_sha256"] != sha(args.protocol)
            or retrieval["retrieval_freeze_sha256"] != sha(args.retrieval_freeze)
            or retrieval["code_sha256"] != freeze["code_sha256"]):
        raise ValueError("retrieval does not match its frozen inputs")
    items = {i["item_id"]: i for i in protocol["items"]}
    for ref in refs:
        item = items[ref["item_id"]]
        if any(ref[k] != item[k] for k in ("history_id", "family", "question")):
            raise ValueError("reference question identity differs from the protocol")
    for row in retrieval["records"]:
        item = items[row["item_id"]]
        validate_selection(row, retrieval["block_catalog"],
                           {s["external_filename"]: s["expected_sha256"] for s in protocol["sources"]})
        for filename, intervals in row["rendered_source_intervals"].items():
            if filename not in item["source_files"]:
                raise ValueError("retrieval includes a source outside the frozen item universe")
            if any(b > len(source_bytes[filename]) for a, b in merged(intervals)):
                raise ValueError("retrieval source interval exceeds source bounds")
        if args.pack_dir is not None:
            pack = args.pack_dir / f"{row['item_id']}_{row['policy']}_{row['budget_words']}.txt"
            if sha(pack) != row["rendered_sha256"] or len(pack.read_text().split()) != row["rendered_words"]:
                raise ValueError("external rendered pack hash or cost mismatch")
    result = evaluate(protocol, refs, retrieval, freeze["policies"], freeze["budgets_words"])
    result["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    def input_name(path):
        path = path.resolve()
        return str(path.relative_to(ROOT)) if ROOT in path.parents else str(path)
    result["inputs"] = {input_name(x): sha(x) for x in [args.protocol, *args.references, args.retrieval,
                                                      args.retrieval_freeze]}
    result["script_sha256"] = sha(__file__)
    result["external_rendered_packs_verified"] = args.pack_dir is not None
    with args.output.open("x") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print(json.dumps(result["summaries"], indent=2))


if __name__ == "__main__":
    main()

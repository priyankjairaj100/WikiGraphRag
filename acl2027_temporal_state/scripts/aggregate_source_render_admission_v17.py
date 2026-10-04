#!/usr/bin/env python3
"""Verify disjoint v17 review coverage; preserve original and recovery decisions."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "data/reader_binding_v17/source_render_natural_protocol_v17.json": "a030efb9dcc9d144eee72595c714dfa64eeb05bccdde0b7fa43b5357f266f4c7",
    "data/reader_binding_v17/source_render_admission_review_plan.json": "8935f5cb446c0e57165907ef78c8c13a8bf9be0b30efc6c53774a412c7283aad",
    "results/source_render_admission_00_05_v17.json": "be21b0bcb80bdd773497419361f46cf678d67924fa0f5ff46eae835f7c400542",
    "results/source_render_admission_root_06_11_v17.json": "112a38382365f6471876e7d678ce26d34f8f9e337036e7b40e0d0fb528db9cc0",
    "data/reader_binding_v17/source_render_telemetry_recovery_protocol_v17_1.json": "f244111b00fd7e9c2dd26d8d619eea43b6e59e8919a363115970838c40a1bfb3",
    "results/source_render_recovery_admission_root_v17_1.json": "abffbabbd0da804623cc345d22e498bdb172a0e0c18106c9c63c4f356ede7887",
}
ADMITTED = "admitted_as_qualified_inspection_view"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count(rows: list[dict]) -> dict:
    return {
        "sources": len({r["source_index"] for r in rows}),
        "blocks": len(rows),
        "tables": sum(r["kind"] == "table" for r in rows),
        "neighbors": sum(r["kind"] == "paragraph" for r in rows),
        "admitted_blocks": sum(r["decision"] == ADMITTED for r in rows),
        "excluded_blocks": sum(r["decision"] == "excluded" for r in rows),
        "admitted_tables": sum(r["decision"] == ADMITTED and r["kind"] == "table" for r in rows),
        "admitted_neighbors": sum(r["decision"] == ADMITTED and r["kind"] == "paragraph" for r in rows),
        "raster_pages_inspected": sum(len(r["raster_page_indices_inspected"]) for r in rows),
    }


def main() -> None:
    target = ROOT / "results/source_render_admission_aggregate_v17.json"
    require(not target.exists(), "Refusing to overwrite an aggregate receipt")
    data = {}
    for name, expected in INPUTS.items():
        require(digest(ROOT / name) == expected, f"Input hash mismatch: {name}")
        data[name] = json.loads((ROOT / name).read_text())
    protocol = data["data/reader_binding_v17/source_render_natural_protocol_v17.json"]
    population = {}
    for index, source in enumerate(protocol["sources"]):
        for block in source["blocks"]:
            key = (index, block["block_id"])
            require(key not in population, "Duplicate original protocol block")
            population[key] = (source, block)
    require(len(population) == 101, "Frozen population must contain all 101 blocks")

    def review_rows(name: str, assigned: list[int]) -> list[dict]:
        receipt = data[name]
        require(receipt["assigned_source_indices"] == assigned, "Reviewer source assignment differs")
        require(receipt["scope"] == {
            "financial_truth_claimed": False, "browser_equivalence_claimed": False,
            "semantic_attachment_verified": False, "complete_evidence_verified": False,
            "questions_authored": 0, "model_calls": 0,
        }, "Review exceeds inspection scope")
        rows = []
        seen = set()
        require({s["source_index"] for s in receipt["sources"]} == set(assigned), "Assigned sources missing")
        for source in receipt["sources"]:
            index = source["source_index"]
            require(source["requested_blocks"] == len(source["blocks"]), "Incomplete requested denominator")
            for block in source["blocks"]:
                key = (index, block["block_id"])
                require(key not in seen and key in population, "Duplicate or foreign reviewed block")
                seen.add(key)
                psource, pblock = population[key]
                require(source["source_path"] == psource["source_path"] and source["source_sha256"] == psource["source_sha256"], "Source identity mismatch")
                require(block["anchor"] == pblock["anchor"] and block["dom_path"] == pblock["dom_path"], "Exact source anchor mismatch")
                kind = "table" if pblock["role"] == "selected_table" else "paragraph"
                require(block["kind"] == kind, "Block kind mismatch")
                require(block["decision"] in (ADMITTED, "excluded"), "Unknown review decision")
                pages = block["raster_page_indices_inspected"]
                require(len(pages) == len(set(pages)) and all(0 <= x < block["raster_pages_available"] for x in pages), "Invalid inspected page accounting")
                rows.append({"source_index": index, "source_path": source["source_path"], "source_sha256": source["source_sha256"], "review_receipt": name, **block})
        totals = count(rows)
        require(all(totals[k] == v for k, v in receipt["totals"].items()), "Receipt totals mismatch")
        return rows

    original = review_rows("results/source_render_admission_00_05_v17.json", list(range(6)))
    original += review_rows("results/source_render_admission_root_06_11_v17.json", list(range(6, 12)))
    original_map = {(r["source_index"], r["block_id"]): r for r in original}
    require(len(original_map) == len(original) == 101 and set(original_map) == set(population), "Original reviews are not disjoint complete coverage")
    recovery = review_rows("results/source_render_recovery_admission_root_v17_1.json", [8, 10])
    recovery_protocol = data["data/reader_binding_v17/source_render_telemetry_recovery_protocol_v17_1.json"]
    expected_recovery = set()
    for source in recovery_protocol["sources"]:
        index = source["original_source_index"]
        for block in source["blocks"]:
            key = (index, block["block_id"])
            require(block == population[key][1], "Recovery protocol changed an original block")
            require(source["source_sha256"] == population[key][0]["source_sha256"], "Recovery source changed")
            expected_recovery.add(key)
    recovery_map = {(r["source_index"], r["block_id"]): r for r in recovery}
    require(len(recovery_map) == 3 and set(recovery_map) == expected_recovery, "Recovery population mismatch")
    for key, row in recovery_map.items():
        prior = original_map[key]
        require(prior["decision"] == "excluded" and prior["reason_codes"] == ["conversion_process_telemetry_failure"], "Recovery is not exactly an original telemetry failure")
        require(row["decision"] == ADMITTED, "Recovery not admitted")
    derived = [recovery_map.get((r["source_index"], r["block_id"]), r) for r in original]
    original_counts, recovery_counts, derived_counts = count(original), count(recovery), count(derived)
    require((original_counts["admitted_blocks"], original_counts["admitted_tables"], original_counts["raster_pages_inspected"]) == (90, 17, 98), "Unexpected original review counts")
    require((derived_counts["admitted_blocks"], derived_counts["admitted_tables"], derived_counts["excluded_blocks"]) == (93, 19, 8), "Unexpected recovery-derived review counts")
    exclusions = [{k: row[k] for k in ("source_index", "source_path", "source_sha256", "block_id", "kind", "anchor", "dom_path", "reason_codes", "review_receipt")} for row in derived if row["decision"] == "excluded"]
    result = {
        "schema_version": "source_render_admission_aggregate_v17",
        "aggregated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_bindings": [{"filename": n, "sha256": h} for n, h in INPUTS.items()] + [{"filename": "scripts/aggregate_source_render_admission_v17.py", "sha256": digest(Path(__file__))}],
        "verification": {"original_reviewer_subsets_disjoint": True, "all_101_frozen_blocks_accounted_for": True, "all_source_identities_dom_locators_and_exact_anchors_match": True, "all_receipt_totals_recomputed": True, "recovery_is_exactly_three_original_telemetry_failures": True},
        "role_statement": "Two agents reviewed disjoint source subsets; this is not duplicate independent annotation or an inter-annotator agreement measurement.",
        "scope": {"qualified_inspection_views_only": True, "financial_truth_claimed": False, "browser_equivalence_claimed": False, "semantic_attachment_verified": False, "complete_evidence_verified": False, "questions_authored": 0, "model_calls": 0},
        "original_attempt": {"counts": original_counts, "excluded_reason_counts_nonexclusive": dict(Counter(reason for row in original if row["decision"] == "excluded" for reason in row["reason_codes"])), "decisions_relabelled": False},
        "separate_telemetry_recovery": {"counts": recovery_counts, "post_failure_selection_disclosed": True, "original_decisions_preserved": True},
        "recovery_derived_availability": {"counts": derived_counts, "description": "Availability using admitted original views plus the three separately admitted telemetry-recovery views; does not replace original-attempt outcomes.", "remaining_exclusions": exclusions},
        "block_lineage": [{"source_index": row["source_index"], "source_path": row["source_path"], "source_sha256": row["source_sha256"], "block_id": row["block_id"], "kind": row["kind"], "dom_path": row["dom_path"], "anchor": row["anchor"], "original_decision": row["decision"], "original_receipt": row["review_receipt"], "separate_recovery_receipt": recovery_map.get((row["source_index"], row["block_id"]), {}).get("review_receipt"), "derived_decision": recovery_map.get((row["source_index"], row["block_id"]), row)["decision"]} for row in original],
    }
    with target.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"receipt": str(target.relative_to(ROOT)), "sha256": digest(target), "original": original_counts, "recovery_derived": derived_counts}, sort_keys=True))


if __name__ == "__main__":
    main()

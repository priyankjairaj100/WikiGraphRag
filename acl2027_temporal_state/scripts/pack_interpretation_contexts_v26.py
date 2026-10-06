#!/usr/bin/env python3
"""Pack fixed v26 rankings using native counts, then validate native prompts.

Full evidence and token arrays stay external. Public records contain selectors,
counts, and hashes. Both stages make zero model generation calls.
"""
import argparse
import json
from pathlib import Path
import time

from build_interpretation_contexts_v26 import canonical, messages, read_rows, sha_bytes, sha_file, write_json

ROOT = Path(__file__).resolve().parents[1]
PACK_RECIPE = {
    "recipe_id": "interpretation_pack_v26",
    "initial_evidence_cap": 512,
    "expanded_evidence_cap": 1280,
    "context_limit": 4096,
    "output_reserve": 768,
    "previous_output_reserve": 768,
    "boundary_reserve": 128,
    "separator_allowance_per_unit": 2,
    "selection": "greedy ranking order; skip units that do not fit; never truncate units",
    "c1_initialization": "all selected C0 units; append other fitting units in ranking order",
    "serialization_order": "ascending original rank",
    "final_check": "exact native full prompts plus prior-output and boundary reserves",
    "overflow": "retain item as preflight failure; never replace or silently shorten it",
    "model_generations": 0,
}


def packet(units, budget):
    units = sorted(units, key=lambda unit: unit["rank"])
    text = "\n\n".join(unit["rendered_text"] for unit in units)
    return {"text": text, "sha256": sha_bytes(text.encode()),
            "evidence_ids": [unit["evidence_id"] for unit in units],
            "units": [{key: unit[key] for key in ["unit_id", "evidence_id", "rank", "source_id",
                "source_sha256", "byte_start", "byte_stop", "span_sha256", "native_tokens"]}
                for unit in units], "budget_tokens": budget,
            "conservative_unit_token_sum": sum(unit["native_tokens"] + 2 for unit in units)}


def create(args):
    candidates = Path(args.candidates).resolve()
    counts_path = Path(args.counts).resolve()
    config_path = Path(args.config).resolve()
    output = Path(args.output).resolve()
    public = Path(args.public_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    public.mkdir(parents=True, exist_ok=True)
    counts = {row["id"]: row for row in read_rows(counts_path)}
    config = json.loads(config_path.read_text())
    rows = list(read_rows(candidates))
    if len(rows) != 60:
        raise ValueError("roster changed")
    freeze = {"created_unix": time.time(), "recipe": PACK_RECIPE,
              "candidate_sha256": sha_file(candidates), "native_counts_sha256": sha_file(counts_path),
              "config_sha256": sha_file(config_path), "script_sha256": sha_file(__file__),
              "message_builder_sha256": sha_file(Path(__file__).with_name("build_interpretation_contexts_v26.py")),
              "prompt_schema_sha256": sha_bytes(canonical({"prompts": config["prompts"],
                                                           "schema": config["output_schema"]}).encode()),
              "prediction_outputs_read": 0, "reference_values_read": 0}
    write_json(public / "context_pack_freeze_v26.json", freeze)
    with (output / "packets_provisional.jsonl").open("x", encoding="utf-8") as stream, \
         (output / "full_prompt_tokenization_requests.jsonl").open("x", encoding="utf-8") as requests:
        for row in rows:
            item = row["item_id"]
            base = {arm: counts[f"{item}.base.{arm}"]["token_count"] for arm in ["I0", "B1", "R1", "F1"]}
            common_room = 4096 - 768 - 768 - 128 - max(base["B1"], base["R1"])
            expanded_budget = max(0, min(1280, common_room))
            initial_budget = max(0, min(512, expanded_budget, 4096 - 768 - 128 - base["I0"]))
            units = []
            for unit in row["candidates"]:
                record = counts[f"{item}.unit.{unit['rank']:02}"]
                if record["mode"] != "raw_text":
                    raise ValueError("unit tokenization mode")
                units.append({**unit, "native_tokens": record["token_count"]})
            initial = []
            initial_cost = 0
            for unit in units:
                cost = unit["native_tokens"] + 2
                if initial_cost + cost <= initial_budget:
                    initial.append(unit)
                    initial_cost += cost
            expanded = list(initial)
            selected_ids = {unit["unit_id"] for unit in initial}
            expanded_cost = initial_cost
            for unit in units:
                if unit["unit_id"] in selected_ids:
                    continue
                cost = unit["native_tokens"] + 2
                if expanded_cost + cost <= expanded_budget:
                    expanded.append(unit)
                    expanded_cost += cost
            c0, c1 = packet(initial, initial_budget), packet(expanded, expanded_budget)
            status = "pending_native_preflight" if c0["units"] and c1["units"] else "empty_evidence_pack"
            record = {"item_id": item, "id": row["id"], "domain": row["domain"],
                      "query": row["query"], "query_sha256": row["query_sha256"],
                      "status": status, "c0": c0, "c1": c1,
                      "base_prompt_tokens": base,
                      "unselected_units": [{"evidence_id": unit["evidence_id"],
                          "native_tokens": unit["native_tokens"], "reason": "does_not_fit_greedy_budget"}
                          for unit in units if unit["unit_id"] not in {u["unit_id"] for u in expanded}]}
            stream.write(canonical(record) + "\n")
            for name, pack in [("C0", c0), ("C1", c1)]:
                requests.write(canonical({"id": f"{item}.pack.{name}", "text": pack["text"]}) + "\n")
            for arm, instruction, pack, previous in [("I0", "initial", c0, None),
                    ("B1", "reextract", c1, ""), ("R1", "revision", c1, ""), ("F1", "initial", c1, None)]:
                requests.write(canonical({"id": f"{item}.full.{arm}", "messages": messages(
                    config, instruction, row["query"], evidence=pack["text"], previous=previous)}) + "\n")
    return {"items": len(rows), "next": "native_zero_completion_preflight",
            "requests": str(output / "full_prompt_tokenization_requests.jsonl"),
            "provisional_packets": str(output / "packets_provisional.jsonl")}


def finalize(args):
    packets_path = Path(args.packets).resolve()
    counts_path = Path(args.counts).resolve()
    output = Path(args.output).resolve()
    public = Path(args.public_dir).resolve()
    counts = {row["id"]: row for row in read_rows(counts_path)}
    rows = list(read_rows(packets_path))
    if len(rows) != 60:
        raise ValueError("roster changed")
    output.mkdir(parents=True, exist_ok=False)
    failures = []
    totals = {"c0_units": 0, "c1_units": 0}
    with (output / "packs.jsonl").open("x", encoding="utf-8") as stream, \
         (public / "context_packs_v26.jsonl").open("x", encoding="utf-8") as meta:
        for row in rows:
            item = row["item_id"]
            item_failures = []
            for key in ["c0", "c1"]:
                row[key]["token_count"] = counts[f"{item}.pack.{key.upper()}"]["token_count"]
                if row[key]["token_count"] > row[key]["budget_tokens"]:
                    item_failures.append(f"{key}_token_budget")
                if sha_bytes(row[key]["text"].encode()) != row[key]["sha256"]:
                    raise ValueError("packet hash changed")
                totals[key + "_units"] += len(row[key]["units"])
            c0_map = {unit["evidence_id"]: unit for unit in row["c0"]["units"]}
            c1_map = {unit["evidence_id"]: unit for unit in row["c1"]["units"]}
            if any(c1_map.get(key) != value for key, value in c0_map.items()):
                raise ValueError("nested source identity changed")
            prompt_counts = {arm: counts[f"{item}.full.{arm}"]["token_count"]
                             for arm in ["I0", "B1", "R1", "F1"]}
            for arm, count in prompt_counts.items():
                reserve = 768 + (768 + 128 if arm in ["B1", "R1"] else 0)
                if count + reserve > 4096:
                    item_failures.append(arm + "_context_budget")
            if not row["c0"]["units"] or not row["c1"]["units"]:
                item_failures.append("empty_evidence_pack")
            row["native_prompt_tokens_without_previous"] = prompt_counts
            row["status"] = "ready" if not item_failures else "preflight_failure"
            row["preflight_failures"] = item_failures
            row["actual_previous_prompt_requires_preflight"] = True
            failures.extend({"item_id": item, "reason": reason} for reason in item_failures)
            stream.write(canonical(row) + "\n")
            metadata = {key: value for key, value in row.items() if key not in ["query", "c0", "c1"]}
            for key in ["c0", "c1"]:
                metadata[key] = {k: v for k, v in row[key].items() if k != "text"}
            meta.write(canonical(metadata) + "\n")
    receipt = {"questions": len(rows), "ready": sum(row["status"] == "ready" for row in rows),
               "preflight_failure_count": len(failures), "failures": failures,
               "counts": totals, "provisional_packets_sha256": sha_file(packets_path),
               "native_full_counts_sha256": sha_file(counts_path),
               "final_packets_sha256": sha_file(output / "packs.jsonl"),
               "public_metadata_sha256": sha_file(public / "context_packs_v26.jsonl"),
               "script_sha256": sha_file(__file__), "model_generations": 0,
               "reference_values_read": 0, "full_text_path": str(output / "packs.jsonl"),
               "meaning": "Native-token-bounded known-document diagnostic packets; no natural retrieval claim."}
    write_json(public / "context_pack_receipt_v26.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("create")
    make.add_argument("--candidates", required=True)
    make.add_argument("--config", default=str(ROOT / "configs/interpretation_v26.json"))
    end = commands.add_parser("finalize")
    end.add_argument("--packets", required=True)
    for subparser in [make, end]:
        subparser.add_argument("--counts", required=True)
        subparser.add_argument("--output", required=True)
        subparser.add_argument("--public-dir", default=str(ROOT / "data/tempo_v26"))
    args = parser.parse_args()
    print(json.dumps(create(args) if args.command == "create" else finalize(args), indent=2))


if __name__ == "__main__":
    main()

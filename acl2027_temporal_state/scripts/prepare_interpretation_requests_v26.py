#!/usr/bin/env python3
"""Freeze reference-free requests before interpretation inference.

Stage one hashes reference files without parsing them. It reads only final
admission labels for selection. Stage two uses exact raw I0 output text.
All source-bearing requests stay outside Git. Public receipts contain hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = {"eligible_resolved", "eligible_ambiguous", "eligible_insufficient"}
PERMUTATIONS = list(itertools.permutations(("B1", "F1", "R1")))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_rows(path, rows):
    with Path(path).open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")


def read_rows(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict):
        value = value.get("records", [value])
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("record_file_schema")
    return value


def external(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT.parent):
        raise ValueError("source_bearing_requests_must_be_external")
    return path


def messages(config, instruction, query, evidence, previous=None):
    parts = ["INSTRUCTION\n" + config["prompts"][instruction],
             "OUTPUT_SCHEMA\n" + canonical(config["output_schema"]),
             "QUESTION\n" + query, "EVIDENCE\n" + evidence]
    if previous is not None:
        parts.append("PREVIOUS\n" + previous)
    return [{"role": "system", "content": config["prompts"]["system"]},
            {"role": "user", "content": "\n\n".join(parts)}]


def request(config, packet, arm, constrained, previous=None):
    instruction = {"I0": "initial", "F1": "initial", "B1": "reextract", "R1": "revision"}[arm]
    evidence = packet["c0" if arm == "I0" else "c1"]["text"]
    result = {"id": packet["item_id"] + "." + arm,
              "messages": messages(config, instruction, packet["query"], evidence, previous)}
    if constrained:
        result["output_schema"] = config["output_schema"]
    return result


def validate_packets(rows):
    seen = set()
    for row in rows:
        item = row["item_id"]
        if item in seen:
            raise ValueError("duplicate_packet_id")
        seen.add(item)
        if text_hash(row["query"]) != row["query_sha256"]:
            raise ValueError("query_digest_mismatch")
        for key in ["c0", "c1"]:
            pack = row[key]
            if text_hash(pack["text"]) != pack["sha256"]:
                raise ValueError("packet_digest_mismatch")
            if len(pack["evidence_ids"]) != len(set(pack["evidence_ids"])):
                raise ValueError("duplicate_evidence_id")
            unit_ids = [unit["evidence_id"] for unit in pack["units"]]
            if unit_ids != pack["evidence_ids"]:
                raise ValueError("packet_unit_ids_mismatch")
        old = {unit["evidence_id"]: unit for unit in row["c0"]["units"]}
        new = {unit["evidence_id"]: unit for unit in row["c1"]["units"]}
        if any(identifier not in new or new[identifier] != unit for identifier, unit in old.items()):
            raise ValueError("c0_not_nested_in_c1")


def first_stage(args):
    paths = {"packets": Path(args.packets).resolve(), "admissions": Path(args.admissions).resolve(),
             "config": Path(args.config).resolve(), "runtime_config": Path(args.runtime_config).resolve(),
             "runtime_script": ROOT / "scripts/run_interpretation_v26.py",
             "scorer_script": ROOT / "scripts/score_interpretations_v26.py",
             "candidate_builder": ROOT / "scripts/build_interpretation_contexts_v26.py",
             "packet_builder": ROOT / "scripts/pack_interpretation_contexts_v26.py",
             "tokenizer_helper": ROOT / "scripts/tokenize_interpretation_v26.py"}
    paths.update({"reference_%03d" % i: Path(path).resolve() for i, path in enumerate(args.references)})
    paths.update({"extra_%03d" % i: Path(path).resolve() for i, path in enumerate(args.extra_freeze)})
    packets = read_rows(paths["packets"])
    validate_packets(packets)
    admissions = read_rows(paths["admissions"])
    labels = {}
    for row in admissions:
        if row["item_id"] in labels:
            raise ValueError("duplicate_admission_id")
        labels[row["item_id"]] = row["eligibility"]
    if set(labels) != {row["item_id"] for row in packets}:
        raise ValueError("packet_admission_roster_mismatch")
    config = json.loads(paths["config"].read_text())
    allowed = set(config["stage_a"]["labels"])
    if set(labels.values()) - allowed:
        raise ValueError("unknown_admission_label")
    selected = [row for row in packets if labels[row["item_id"]] in ELIGIBLE]
    if not selected:
        raise ValueError("no_finally_eligible_items")
    if len(packets) > config["maximum_questions"]:
        raise ValueError("roster_over_budget")
    output = external(args.output)
    public = Path(args.public_receipt).resolve()
    if public.exists() or output.exists():
        raise ValueError("freeze_destination_exists")
    output.mkdir(parents=True)
    public.parent.mkdir(parents=True, exist_ok=True)
    requests = [request(config, packet, "I0", args.constrained_decoding) for packet in selected]
    write_rows(output / "I0_requests.jsonl", requests)
    orders = {row["item_id"]: list(PERMUTATIONS[i % len(PERMUTATIONS)]) for i, row in enumerate(selected)}
    freeze = {"schema_version": "interpretation_requests_freeze_v26", "created_at_unix": time.time(),
              "renderer_sha256": digest(__file__), "input_paths": {key: str(path) for key, path in paths.items()},
              "input_sha256": {key: digest(path) for key, path in paths.items()},
              "roster_ids": [row["item_id"] for row in packets],
              "eligible_ids": [row["item_id"] for row in selected], "admission_labels": labels,
              "constrained_decoding_all_arms": args.constrained_decoding,
              "I0_requests_sha256": digest(output / "I0_requests.jsonl"),
              "second_stage_order_rule": "six permutations, cyclic assignment in frozen packet order",
              "second_stage_order": orders, "previous_rule": "exact raw response content; malformed JSON is unchanged",
              "missing_draft_rule": "block B1 and R1; prepare independent F1",
              "reference_classes_parsed": False, "native_completion_calls": 0}
    write(output / "freeze.json", freeze)
    receipt = {key: value for key, value in freeze.items() if key != "input_paths"}
    receipt.update(freeze_sha256=digest(output / "freeze.json"),
                   external_requests_path=str(output / "I0_requests.jsonl"),
                   raw_source_text_in_public_receipt=False)
    write(public, receipt)
    return {"status": "I0_requests_frozen", "eligible_items": len(selected),
            "requests_path": str(output / "I0_requests.jsonl"), "freeze_sha256": receipt["freeze_sha256"]}


def check_freeze(folder):
    folder = Path(folder).resolve()
    frozen = json.loads((folder / "freeze.json").read_text())
    if digest(__file__) != frozen["renderer_sha256"]:
        raise ValueError("renderer_changed")
    for key, path in frozen["input_paths"].items():
        if digest(path) != frozen["input_sha256"][key]:
            raise ValueError("frozen_input_changed_" + key)
    if digest(folder / "I0_requests.jsonl") != frozen["I0_requests_sha256"]:
        raise ValueError("I0_requests_changed")
    return frozen


def second_stage(args):
    first = Path(args.first_stage).resolve()
    if digest(first / "freeze.json") != args.freeze_sha256:
        raise ValueError("request_freeze_digest_mismatch")
    frozen = check_freeze(first)
    i0_run = Path(args.i0_run).resolve()
    runtime_freeze = json.loads((i0_run / "freeze.json").read_text())
    if runtime_freeze["requests_sha256"] != frozen["I0_requests_sha256"]:
        raise ValueError("I0_run_used_different_requests")
    if runtime_freeze["config_sha256"] != frozen["input_sha256"]["runtime_config"]:
        raise ValueError("I0_run_used_different_runtime")
    if runtime_freeze["script_sha256"] != frozen["input_sha256"]["runtime_script"]:
        raise ValueError("I0_run_used_different_runner")
    originals = {row["id"]: row for row in read_rows(first / "I0_requests.jsonl")}
    packets = {row["item_id"]: row for row in read_rows(frozen["input_paths"]["packets"])}
    config = json.loads(Path(frozen["input_paths"]["config"]).read_text())
    rows = []
    accounting = []
    i0_hashes = {}
    for item in frozen["eligible_ids"]:
        directory = i0_run / (item + ".I0")
        result_file = directory / "result.json"
        if not result_file.exists():
            raise ValueError("I0_not_terminal_" + item)
        result = json.loads(result_file.read_text())
        i0_hashes[item] = {"result_sha256": digest(result_file), "status": result["status"]}
        raw_file = directory / "response.raw.json"
        previous = None
        if raw_file.exists():
            actual_input = json.loads((directory / "input.json").read_text())
            if actual_input != originals[item + ".I0"]:
                raise ValueError("I0_item_request_changed")
            raw_digest = digest(raw_file)
            if result.get("raw_response_sha256") != raw_digest:
                raise ValueError("I0_response_digest_mismatch")
            raw_response = json.loads(raw_file.read_text())
            previous = raw_response.get("content")
            if not isinstance(previous, str):
                previous = None
            i0_hashes[item].update(raw_response_sha256=raw_digest,
                                  content_sha256=None if previous is None else text_hash(previous))
        for arm in frozen["second_stage_order"][item]:
            if arm in {"B1", "R1"} and previous is None:
                accounting.append({"id": item + "." + arm, "status": "blocked_missing_I0_response"})
                continue
            row = request(config, packets[item], arm, frozen["constrained_decoding_all_arms"],
                          previous if arm in {"B1", "R1"} else None)
            rows.append(row)
            accounting.append({"id": row["id"], "status": "prepared"})
    output = external(args.output)
    public = Path(args.public_receipt).resolve()
    if output.exists() or public.exists():
        raise ValueError("second_stage_destination_exists")
    output.mkdir(parents=True)
    public.parent.mkdir(parents=True, exist_ok=True)
    write_rows(output / "expanded_requests.jsonl", rows)
    receipt = {"schema_version": "interpretation_expanded_requests_v26", "created_at_unix": time.time(),
               "first_stage_freeze_sha256": args.freeze_sha256, "renderer_sha256": digest(__file__),
               "I0_runtime_freeze_sha256": digest(i0_run / "freeze.json"), "I0_records": i0_hashes,
               "requests_sha256": digest(output / "expanded_requests.jsonl"),
               "accounting": accounting, "prepared_requests": len(rows),
               "constrained_decoding_all_arms": frozen["constrained_decoding_all_arms"],
               "literal_previous_content_preserved": True, "reference_classes_parsed": False,
               "native_completion_calls": 0, "external_requests_path": str(output / "expanded_requests.jsonl")}
    write(output / "receipt.json", receipt)
    write(public, receipt)
    return {"status": "expanded_requests_prepared", "requests": len(rows),
            "requests_path": str(output / "expanded_requests.jsonl"),
            "receipt_sha256": digest(output / "receipt.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    first = commands.add_parser("initial")
    first.add_argument("--packets", required=True)
    first.add_argument("--admissions", required=True)
    first.add_argument("--references", nargs="+", required=True)
    first.add_argument("--extra-freeze", nargs="*", default=[])
    first.add_argument("--config", default=str(ROOT / "configs/interpretation_v26.json"))
    first.add_argument("--runtime-config", default=str(ROOT / "configs/interpreter_runtime_v26.json"))
    first.add_argument("--constrained-decoding", action="store_true")
    first.add_argument("--output", required=True)
    first.add_argument("--public-receipt", required=True)
    second = commands.add_parser("expanded")
    second.add_argument("--first-stage", required=True)
    second.add_argument("--freeze-sha256", required=True)
    second.add_argument("--i0-run", required=True)
    second.add_argument("--output", required=True)
    second.add_argument("--public-receipt", required=True)
    args = parser.parse_args()
    result = first_stage(args) if args.command == "initial" else second_stage(args)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

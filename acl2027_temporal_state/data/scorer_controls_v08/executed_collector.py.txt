#!/usr/bin/env python3
"""Frozen cold/warm technical control; default operation is offline verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.request

import local_backend_v07 as backend

PROJECT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT / "configs/scorer_controls_v08.json"
DATA = PROJECT / "data/scorer_controls_v08"
RECEIPT = PROJECT / "results/scorer_controls_v08_receipt.json"
SUMMARY = PROJECT / "results/scorer_controls_v08.json"
FROZEN = ["configs/scorer_controls_v08.json", "scripts/scorer_controls_v08.py",
          "scripts/local_backend_v07.py", "configs/local_backend_v07.json",
          "data/likelihood_scoring_v07/request.json",
          "data/likelihood_scoring_v07/rendered_prompts.jsonl",
          "data/likelihood_scoring_v07/backend_binding.json",
          "results/local_backend_v07_softmax_evidence.txt",
          "results/local_backend_v07_vocab_fallback_evidence.txt"]


def load(path):
    return json.loads(Path(path).read_text())


def plan():
    config = load(CONFIG)
    request = load(PROJECT / FROZEN[4])
    first = {}
    for item in request["items"]:
        if item["condition_id"] == "yes_no":
            first.setdefault(item["kind"], item["item_id"])
    selected = [(x["kind"], x["item_id"]) for x in config["selected_items"]]
    if selected != list(first.items()):
        raise ValueError("subset is not the first original-order item of each kind")
    prompts = {(x["item_id"], x["condition_id"]): x for x in
               map(json.loads, (PROJECT / FROZEN[5]).read_text().splitlines())}
    requested = {(x["item_id"], x["condition_id"]): x for x in request["items"]}
    rows = []
    for item in config["selected_items"]:
        for condition in item["condition_order"]:
            key = (item["item_id"], condition)
            for cache in ("cold", "warm"):
                rows.append({"item_id": key[0], "kind": item["kind"], "condition_id": key[1],
                             "cache_condition": cache, "process_index": len(rows) // 2,
                             "prompt": prompts[key], "messages": requested[key]["messages"]})
    if len(rows) != config["model_distributions"] or len(rows) // 2 != config["fresh_processes"]:
        raise ValueError("planned coverage mismatch")
    return config, rows


def prepare():
    config, rows = plan()
    if DATA.exists() and any(DATA.iterdir()):
        raise ValueError("refusing to overwrite an existing frozen control directory")
    DATA.mkdir(parents=True, exist_ok=True)
    output = {"schema_version": "scorer_controls_frozen_inputs_v0.8",
              "declaration": "Designed after observing v0.7 aggregate results; frozen before v0.8 execution.",
              "source_hashes": {p: backend.digest(PROJECT / p) for p in FROZEN},
              "execution_order": [{k: r[k] for k in ("item_id", "kind", "condition_id", "cache_condition", "process_index")} for r in rows],
              "created_unix_seconds": time.time(), "completed_distributions_at_freeze": 0}
    backend.write_json(DATA / "frozen_inputs.json", output)
    (DATA / "executed_collector.py.txt").write_bytes(Path(__file__).read_bytes())
    return output


def verify_frozen():
    frozen = load(DATA / "frozen_inputs.json")
    if set(frozen["source_hashes"]) != set(FROZEN):
        raise ValueError("unexpected frozen input paths")
    for path, digest in frozen["source_hashes"].items():
        if backend.digest(PROJECT / path) != digest:
            raise ValueError(f"frozen input changed: {path}")
    if backend.digest(DATA / "executed_collector.py.txt") != frozen["source_hashes"]["scripts/scorer_controls_v08.py"]:
        raise ValueError("executed collector snapshot mismatch")
    _, rows = plan()
    actual = [{k: r[k] for k in ("item_id", "kind", "condition_id", "cache_condition", "process_index")} for r in rows]
    if frozen["execution_order"] != actual:
        raise ValueError("frozen execution order mismatch")
    return frozen


def extract(row, request, response):
    """Strictly audit the actual token work and extract unnormalized label LPs."""
    tokens = row["prompt"]["input_token_ids"]
    expected = backend.one_token_request(tokens)
    expected["cache_prompt"] = row["cache_condition"] == "warm"
    if request != expected:
        raise ValueError("request differs from fixed full-vocabulary completion policy")
    n = len(tokens)
    if response.get("truncated") is not False or response.get("tokens_evaluated") != n or response.get("tokens_predicted") != 1:
        raise ValueError("truncated response or token count mismatch")
    timing = response["timings"]
    cache, prompt = timing["cache_n"], timing["prompt_n"]
    if type(cache) is not int or type(prompt) is not int or cache < 0 or prompt < 1 or cache + prompt != n:
        raise ValueError("prefix cache accounting mismatch")
    if row["cache_condition"] == "cold" and (cache != 0 or prompt != n):
        raise ValueError("cold request reused a prefix")
    if row["cache_condition"] == "warm" and cache <= 0:
        raise ValueError("warm request did not reuse its prefix")
    distributions = response["completion_probabilities"]
    if len(distributions) != 1:
        raise ValueError("expected one next-token distribution")
    top = distributions[0]["top_logprobs"]
    if len({x["id"] for x in top}) != len(top):
        raise ValueError("duplicate probability token")
    probabilities = {x["id"]: x["logprob"] for x in top}
    yes, no = probabilities[9454], probabilities[2753]
    if not all(isinstance(x, (float, int)) and math.isfinite(x) and x <= 0 for x in (yes, no)):
        raise ValueError("invalid label log probabilities")
    mass = math.exp(yes) + math.exp(no)
    if mass > 1 + 1e-6:
        raise ValueError("literal label mass exceeds one")
    return {k: row[k] for k in ("item_id", "kind", "condition_id", "cache_condition", "process_index")} | {
        "input_tokens": n, "cache_tokens": cache, "prompt_tokens": prompt,
        "yes_logprob": yes, "no_logprob": no, "logodds": yes - no,
        "literal_label_mass": mass, "prompt_ms": timing["prompt_ms"]}


def collect(asset_root):
    frozen = verify_frozen()
    config, rows = plan()
    if RECEIPT.exists() or (DATA / "raw_responses.jsonl").exists():
        raise ValueError("refusing to overwrite a control run or partial attempt")
    receipt = {"schema_version": "scorer_controls_receipt_v0.8", "status": "started",
               "frozen_inputs_sha256": backend.digest(DATA / "frozen_inputs.json"),
               "started_unix_seconds": time.time(), "completed_distributions": 0,
               "processes": [], "error": None}
    backend.write_json(RECEIPT, receipt)
    raw_path = DATA / "raw_responses.jsonl"
    try:
        with raw_path.open("w") as out:
            for index in range(0, len(rows), 2):
                cold, warm = rows[index:index + 2]
                number = cold["process_index"]
                prefix = DATA / f"process_{number:02d}"
                with backend.server(asset_root=asset_root, context=config["context_tokens"],
                                    port=18088, log_path=prefix.with_suffix(".server.log"),
                                    cpu_seconds=config["cpu_seconds_per_process"]) as execution:
                    receipt["processes"].append(execution)
                    backend.write_json(RECEIPT, receipt)
                    port = execution["port"]
                    props = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/props", timeout=30))
                    backend.write_json(prefix.with_suffix(".props.json"), props)
                    old_binding = load(PROJECT / FROZEN[6])
                    if hashlib.sha256(props["chat_template"].encode()).hexdigest() != old_binding["chat_template_sha256"]:
                        raise ValueError("native chat template changed")
                    rendered = backend.render_and_tokenize(port, cold["messages"])
                    for key in rendered:
                        if rendered[key] != cold["prompt"][key]:
                            raise ValueError("fresh rendered prompt does not match v0.7 bytes/tokens")
                    for label, token in (("Yes", 9454), ("No", 2753)):
                        appended = backend.post(port, "tokenize", {"content": rendered["rendered_prompt"] + label,
                            "add_special": True, "parse_special": True})["tokens"]
                        if appended != rendered["input_token_ids"] + [token]:
                            raise ValueError("exact single-token label boundary changed")
                    for row in (cold, warm):
                        payload = backend.one_token_request(row["prompt"]["input_token_ids"])
                        payload["cache_prompt"] = row["cache_condition"] == "warm"
                        response = backend.post(port, "completion", payload, config["per_request_timeout_seconds"])
                        record = {k: row[k] for k in ("item_id", "kind", "condition_id", "cache_condition", "process_index")}
                        record.update({"request": payload, "response": response})
                        out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                        out.flush()
                        measured = extract(row, payload, response)
                        receipt["completed_distributions"] += 1
                        backend.write_json(RECEIPT, receipt)
                        print(json.dumps({"completed": receipt["completed_distributions"], "of": len(rows),
                                          "item_id": row["item_id"], "condition": row["condition_id"],
                                          "cache": row["cache_condition"], "prompt_ms": measured["prompt_ms"]}), flush=True)
                backend.write_json(prefix.with_suffix(".execution.json"), execution)
                backend.write_json(RECEIPT, receipt)
        verify_frozen()
        receipt.update({"status": "completed", "finished_unix_seconds": time.time(),
                        "raw_responses_sha256": backend.digest(raw_path),
                        "process_artifact_hashes": {str(p.relative_to(PROJECT)): backend.digest(p) for p in sorted(DATA.glob("process_*"))}})
    except BaseException as error:
        receipt.update({"status": "failed", "error": str(error), "error_type": type(error).__name__,
                        "finished_unix_seconds": time.time()})
        backend.write_json(RECEIPT, receipt)
        raise
    backend.write_json(RECEIPT, receipt)
    return verify()


def verify():
    verify_frozen()
    config, rows = plan()
    receipt = load(RECEIPT)
    if receipt["status"] != "completed" or receipt["completed_distributions"] != len(rows):
        raise ValueError("control collection is not complete")
    if receipt["frozen_inputs_sha256"] != backend.digest(DATA / "frozen_inputs.json"):
        raise ValueError("receipt freeze binding mismatch")
    raw = DATA / "raw_responses.jsonl"
    if receipt["raw_responses_sha256"] != backend.digest(raw):
        raise ValueError("raw response hash mismatch")
    expected_paths = {str(p.relative_to(PROJECT)) for p in DATA.glob("process_*")}
    if set(receipt["process_artifact_hashes"]) != expected_paths or len(expected_paths) != 24:
        raise ValueError("process artifact coverage mismatch")
    for path, sha in receipt["process_artifact_hashes"].items():
        if backend.digest(PROJECT / path) != sha:
            raise ValueError("process artifact hash mismatch")
    records = list(map(json.loads, raw.read_text().splitlines()))
    if len(records) != len(rows):
        raise ValueError("raw response coverage mismatch")
    measured = []
    for row, record in zip(rows, records):
        for key in ("item_id", "kind", "condition_id", "cache_condition", "process_index"):
            if record[key] != row[key]:
                raise ValueError("raw response order mismatch")
        measured.append(extract(row, record["request"], record["response"]))
    lookup = {(x["item_id"], x["condition_id"], x["cache_condition"]): x for x in measured}
    comparisons = []
    tolerance = config["numerical_comparison_absolute_tolerance"]
    for item in config["selected_items"]:
        ident = item["item_id"]
        cold_delta = lookup[(ident, "no_yes", "cold")]["logodds"] - lookup[(ident, "yes_no", "cold")]["logodds"]
        warm_delta = lookup[(ident, "no_yes", "warm")]["logodds"] - lookup[(ident, "yes_no", "warm")]["logodds"]
        within = {}
        for condition in ("yes_no", "no_yes"):
            cold, warm = (lookup[(ident, condition, cache)] for cache in ("cold", "warm"))
            within[condition] = {key: warm[key] - cold[key] for key in ("yes_logprob", "no_logprob", "logodds")}
            within[condition]["both_labels_within_tolerance"] = all(abs(within[condition][key]) <= tolerance for key in ("yes_logprob", "no_logprob"))
        comparisons.append({"item_id": ident, "kind": item["kind"], "cold_no_yes_minus_yes_no_logodds": cold_delta,
                            "warm_no_yes_minus_yes_no_logodds": warm_delta, "interaction": warm_delta - cold_delta,
                            "warm_minus_cold": within,
                            "cold_label_order_sign_change": lookup[(ident, "yes_no", "cold")]["logodds"] * lookup[(ident, "no_yes", "cold")]["logodds"] < 0})
    output = {"schema_version": "scorer_controls_result_v0.8", "status": "passed_artifact_and_accounting_validation",
              "new_model_executions_in_this_replay": 0, "collected_model_distributions": len(measured),
              "source_histories": 1, "unique_items": 4, "fresh_processes": 8,
              "measurements": measured, "comparisons": comparisons,
              "cold_label_order_sign_changes": sum(x["cold_label_order_sign_change"] for x in comparisons),
              "cold_warm_pairs_within_tolerance": sum(v["both_labels_within_tolerance"] for x in comparisons for v in x["warm_minus_cold"].values()),
              "numerical_absolute_tolerance": tolerance, "accuracy_measured": False,
              "elapsed_seconds": receipt["finished_unix_seconds"] - receipt["started_unix_seconds"],
              "receipt_sha256": backend.digest(RECEIPT), "claim_limits": config["claim_limits"]}
    backend.write_json(SUMMARY, output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--collect", action="store_true")
    parser.add_argument("--assets", type=Path, default=backend.DEFAULT_ASSETS)
    args = parser.parse_args()
    result = prepare() if args.prepare else collect(args.assets) if args.collect else verify()
    print(json.dumps({k: v for k, v in result.items() if k not in ("measurements", "source_hashes", "execution_order")}))

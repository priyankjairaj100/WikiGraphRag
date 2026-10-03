#!/usr/bin/env python3
"""Run synthetic contract checks, not a research benchmark or model evaluation."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.evaluation import GoldAnswer, evaluate_prediction
from temporal_state.memory import build_memory
from temporal_state.models import Assertion, Question, Source


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def read_source(record):
    return Source(**record)


def read_assertion(record):
    record = dict(record)
    record["context_source_ids"] = tuple(record.get("context_source_ids", ()))
    return Assertion(**record)


def read_gold(record):
    record = dict(record)
    for key in ("accepted_value_sets", "sufficient_evidence_sets"):
        record[key] = tuple(tuple(values) for values in record.get(key, ()))
    return GoldAnswer(**record)


def run(input_path: Path, policies: tuple[str, ...]) -> dict:
    raw = input_path.read_bytes()
    fixture = json.loads(raw)
    if fixture.get("purpose") != "synthetic_contract_checks":
        raise ValueError("This runner accepts explicitly marked synthetic contract fixtures only")
    config = {
        "runner_version": "0.1",
        "policies": list(policies),
        "input_schema_version": fixture["schema_version"],
        "prediction_inputs": "raw_question_plus_sources_and_hand_authored_predicted_assertions",
        "scoring": "whole_value_sets_and_complete_annotated_source_support",
        "cutoff_rule": "source.available_at <= question.information_cutoff",
        "gold_routing": False,
        "natural_data_experiments": "pending",
    }
    rows = []
    source_cutoffs = set()
    summary = {policy: {"grounded_checks_passed": 0, "grounded_checks_total": 0,
                        "status_checks_passed": 0, "status_checks_total": 0} for policy in policies}
    construction_checks = []
    for case in fixture["cases"]:
        sources = [read_source(item) for item in case["sources"]]
        assertions = [read_assertion(item) for item in case["predicted_assertions"]]
        if "expected_build_error" in case:
            cutoff = case["build_cutoff"]
            source_cutoffs.add(cutoff)
            try:
                build_memory(sources, assertions, cutoff)
            except ValueError as exc:
                construction_checks.append({"case_id": case["case_id"], "passed": True,
                    "expected_error": "ValueError", "observed_error": type(exc).__name__,
                    "message": str(exc), "source_cutoff": cutoff})
            else:
                construction_checks.append({"case_id": case["case_id"], "passed": False,
                    "expected_error": "ValueError", "observed_error": None, "source_cutoff": cutoff})
            continue
        for item in case["questions"]:
            question = Question(**item)
            cutoff = question.information_cutoff
            source_cutoffs.add(cutoff)
            memory = build_memory(sources, assertions, cutoff)
            # Inference has no access to any gold labels or gold routing keys.
            predictions = {policy: memory.answer(question, policy=policy) for policy in policies}
            gold_records = {record["question_id"]: record for record in case["gold_answers"]}
            gold = read_gold(gold_records[question.question_id]) if question.question_id in gold_records else None
            for policy, prediction in predictions.items():
                row = {"case_id": case["case_id"], "question": asdict(question),
                       "policy": policy, "prediction": asdict(prediction),
                       "source_cutoff": cutoff,
                       "memory_diagnostics": getattr(memory, "diagnostics", {}),
                       "n_episodes": len(memory.episodes)}
                if gold is not None:
                    score = evaluate_prediction(question, prediction, gold, sources)
                    row["gold"] = asdict(gold)
                    row["score"] = score.to_dict()
                    summary[policy]["grounded_checks_total"] += 1
                    summary[policy]["grounded_checks_passed"] += int(score.correct)
                elif "expected_status" in case:
                    passed = prediction.status == case["expected_status"]
                    row["status_check"] = {"expected": case["expected_status"], "passed": passed}
                    summary[policy]["status_checks_total"] += 1
                    summary[policy]["status_checks_passed"] += int(passed)
                else:
                    raise ValueError(f"No gold or status contract for {question.question_id}")
                rows.append(row)
    code_paths = sorted((ROOT / "src" / "temporal_state").glob("*.py")) + [Path(__file__).resolve()]
    code_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in code_paths}
    primary = summary.get("episodes")
    primary_passed = None if primary is None else (
        primary["grounded_checks_passed"] == primary["grounded_checks_total"]
        and primary["status_checks_passed"] == primary["status_checks_total"]
        and all(item["passed"] for item in construction_checks)
    )
    return {
        "report_type": "synthetic_contract_checks",
        "notice": "Hand-authored diagnostics only. These counts are NOT research benchmark accuracy.",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "config": config,
        "config_sha256": canonical_hash(config),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_file": str(input_path.resolve()),
        "code_sha256": canonical_hash(code_hashes),
        "code_file_sha256": code_hashes,
        "source_cutoffs": sorted(source_cutoffs),
        "all_primary_contracts_passed": primary_passed,
        "limitations": fixture["limitations"] + [
            "Grounding is scored against annotated source sets; the scorer does not independently prove entailment.",
            "Fixture design favors exercising temporal semantics; baseline differences are not empirical research gains.",
            "No statistical significance, model calibration, training, or natural-data generalization is claimed.",
        ],
        "synthetic_contract_checks": summary,
        "construction_checks": construction_checks,
        "records": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/diagnostics/fixtures.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/diagnostics/results.json")
    parser.add_argument("--policies", nargs="+", choices=("episodes", "newest_available"),
                        default=["episodes", "newest_available"])
    args = parser.parse_args()
    result = run(args.input, tuple(args.policies))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"report_type": result["report_type"],
                      "all_primary_contracts_passed": result["all_primary_contracts_passed"],
                      "synthetic_contract_checks": result["synthetic_contract_checks"],
                      "construction_checks": result["construction_checks"],
                      "output": str(args.output.resolve())}, indent=2))
    if result["all_primary_contracts_passed"] is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

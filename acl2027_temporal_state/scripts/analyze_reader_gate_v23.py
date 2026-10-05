#!/usr/bin/env python3
"""Strict scorer for the v23 preliminary typed reader controls, not natural QA."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from prepare_reader_gate_v23 import canonical, digest, bytes_digest, exact_decimal, render_request, write_new, ROOT

KEYS = {"status", "value", "unit", "operation", "operand_evidence_ids", "evidence_ids"}


def parse_prediction(raw):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValueError("prediction must be bounded raw text")
    try:
        value = json.loads(raw, object_pairs_hook=no_duplicates)
    except RecursionError as exc:
        # A bounded but deeply nested completion remains a failed scheduled
        # output; it must not terminate analysis of the whole frozen attempt.
        raise ValueError("prediction JSON exceeds parser nesting limit") from exc
    if not isinstance(value, dict) or set(value) != KEYS:
        raise ValueError("wrong response keys")
    if value["status"] not in {"answer", "abstain"}:
        raise ValueError("invalid status")
    for key in ("operand_evidence_ids", "evidence_ids"):
        if not isinstance(value[key], list) or any(not isinstance(s, str) for s in value[key]):
            raise ValueError("invalid evidence IDs")
        if len(set(value[key])) != len(value[key]):
            raise ValueError("duplicate evidence ID")
    if value["status"] == "abstain":
        if any(value[k] is not None for k in ("value", "unit", "operation")) or value["evidence_ids"] or value["operand_evidence_ids"]:
            raise ValueError("abstention contains answer claims")
    else:
        if not isinstance(value["value"], str) or not isinstance(value["unit"], dict):
            raise ValueError("invalid amount/unit")
        # A JSON string amount permits exact Decimal evaluation, never float math.
        if "e" in value["value"].lower():
            raise ValueError("exponent output forbidden by frozen prompt")
        exact_decimal("identity", [value["value"]])
        if value["operation"] not in {"identity", "subtract"}:
            raise ValueError("invalid operation")
    return value


def score_item(item, raw):
    try:
        prediction = parse_prediction(raw)
    except (ValueError, TypeError, InvalidOperation) as exc:
        return {"valid": False, "joint_correct": False, "substantive_answer": None, "reason": str(exc)}
    ref = item["reference"]
    substantive = prediction["status"] == "answer"
    if not substantive:
        return {"valid": True, "joint_correct": ref["status"] == "abstain", "substantive_answer": False,
                "reason": "abstained"}
    available = {e["evidence_id"]: e for e in item["evidence"]}
    ids = prediction["operand_evidence_ids"]
    if any(e not in available for e in prediction["evidence_ids"] + ids):
        return {"valid": False, "joint_correct": False, "substantive_answer": True, "reason": "fabricated evidence ID"}
    if set(ids) != set(prediction["evidence_ids"]):
        return {"valid": False, "joint_correct": False, "substantive_answer": True, "reason": "inconsistent operand citations"}
    try:
        computed = exact_decimal(prediction["operation"], [available[e]["record"]["normalized_value"] for e in ids])
    except ValueError as exc:
        return {"valid": False, "joint_correct": False, "substantive_answer": True, "reason": str(exc)}
    correct = (ref["status"] == "answer" and prediction["operation"] == ref["operation"]
               and ids == ref["operand_evidence_ids"] and prediction["unit"] == ref["unit"]
               and Decimal(prediction["value"]) == Decimal(ref["value"]) == Decimal(computed))
    return {"valid": True, "joint_correct": correct, "substantive_answer": True,
            "reason": "joint_answer_check_passed" if correct else "wrong_or_unsupported_answer"}


def score_attempt(items, predictions, protocol):
    ids = {i["item_id"] for i in items}
    if len(ids) != len(items) or len({p["item_id"] for p in predictions}) != len(predictions):
        raise ValueError("duplicate item/prediction IDs")
    if {p["item_id"] for p in predictions} != ids:
        raise ValueError("missing or extra predictions; attempt incomplete")
    index = {p["item_id"]: p for p in predictions}
    counts = Counter()
    strata = defaultdict(Counter)
    histories = defaultdict(Counter)
    outcomes = []
    for item in items:
        p = index[item["item_id"]]
        if p["request_sha256"] != item["request_sha256"]:
            raise ValueError("request hash mismatch")
        result = score_item(item, p["raw_response"])
        condition = item["evidence_condition"]
        counts["valid"] += result["valid"]
        counts[condition] += 1
        counts[condition + "_correct"] += result["joint_correct"]
        counts[condition + "_substantive"] += result["substantive_answer"] is True
        if condition == "complete":
            strata[item["stratum"]]["total"] += 1
            strata[item["stratum"]]["joint_correct"] += result["joint_correct"]
        histories[item["history_id"]][condition + "_total"] += 1
        histories[item["history_id"]][condition + "_correct"] += result["joint_correct"]
        outcomes.append({"item_id": item["item_id"], **result})
    g = protocol["gate"]
    passed = (counts["complete"] == 100 and counts["designed_insufficient"] == 100
              and counts["valid"] == g["valid_predictions_required"]
              and counts["complete_correct"] >= g["complete_joint_correct_minimum"]
              and counts["designed_insufficient_correct"] == g["insufficient_valid_abstentions_required"]
              and counts["designed_insufficient_substantive"] <= g["insufficient_substantive_answers_maximum"]
              and set(strata) == {"reported_fact", "same_filing_difference"}
              and all(v["total"] == 50 and v["joint_correct"] >= g["each_complete_stratum_correct_minimum"] for v in strata.values()))
    return {"schema_version": "reader_gate_analysis_v23", "control_thresholds_met": passed,
            "natural_reader_gate_open": False, "comparison_authorized": False,
            "reason": "typed controls alone cannot establish natural text/table reader competence",
            "counts": dict(counts), "strata": {k: dict(v) for k, v in strata.items()},
            "histories": {k: dict(v) for k, v in histories.items()}, "outcomes": outcomes,
            "independent_risk_confidence_interval": None,
            "scope": "development control diagnostic; histories clustered; no held-out gain"}


def validate_preflight(preflight, protocol, manifest, items, requests):
    """A preflight is an auditable receipt, not proof that its assertions are true."""
    if preflight.get("protocol_sha256") != manifest["protocol_sha256"]:
        raise ValueError("preflight protocol mismatch")
    if preflight.get("items_content_sha256") != digest(items) or preflight.get("requests_content_sha256") != digest(requests):
        raise ValueError("preflight data mismatch")
    if preflight.get("model") != {k: protocol["model"][k] for k in ("repository", "revision", "dtype", "quantization")}:
        raise ValueError("preflight model mismatch")
    for key in ("runtime_artifact_sha256", "hardware_description", "review_record_sha256", "frozen_before_prediction_at_utc"):
        if not preflight.get(key) or "PENDING" in str(preflight[key]).upper():
            raise ValueError("unresolved preflight: " + key)
    if preflight.get("decoding") != protocol["decoding"]:
        raise ValueError("decoding changed")
    if preflight.get("reviewed_item_ids") != sorted(i["item_id"] for i in items):
        raise ValueError("not all items source/reference reviewed")
    if preflight.get("predictions_at_freeze") != 0:
        raise ValueError("freeze after predictions")
    counts = preflight.get("request_token_counts", {})
    if set(counts) != {i["item_id"] for i in items}:
        raise ValueError("token counts incomplete")
    if any(type(v) is not int or v < 1 or v + protocol["decoding"]["max_new_tokens"] > protocol["decoding"]["total_context_tokens"] for v in counts.values()):
        raise ValueError("context overflow or invalid token count")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path, help="JSON list: item_id, request_sha256, raw_response")
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/reader_gate_v23/item_manifest.json")
    parser.add_argument("--protocol", type=Path, default=ROOT / "data/reader_gate_v23/protocol.json")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = args.protocol.read_bytes()
    protocol = json.loads(raw)
    manifest = json.loads(args.manifest.read_text())
    items = json.loads(args.items.read_text())["items"]
    if bytes_digest(raw) != manifest["protocol_sha256"] or digest(items) != manifest["items_content_sha256"]:
        raise ValueError("frozen input mismatch")
    requests = [{"item_id": i["item_id"], "request_sha256": i["request_sha256"], "request": render_request(i, protocol)} for i in items]
    if digest(requests) != manifest["requests_content_sha256"]:
        raise ValueError("renderer output mismatch")
    preflight = json.loads(args.preflight.read_text())
    validate_preflight(preflight, protocol, manifest, items, requests)
    predictions = json.loads(args.predictions.read_text())
    result = score_attempt(items, predictions, protocol)
    result["provenance"] = {"protocol_sha256": bytes_digest(raw), "manifest_sha256": bytes_digest(args.manifest.read_bytes()),
                            "predictions_sha256": bytes_digest(args.predictions.read_bytes()), "preflight_sha256": bytes_digest(args.preflight.read_bytes())}
    write_new(args.output, result)
    print(json.dumps({k: result[k] for k in ("control_thresholds_met", "natural_reader_gate_open", "comparison_authorized", "counts")}, indent=2))


if __name__ == "__main__":
    main()

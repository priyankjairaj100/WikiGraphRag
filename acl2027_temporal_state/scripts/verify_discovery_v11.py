#!/usr/bin/env python3
"""Offline, metadata-only verification of the bounded v0.11 discovery register.

This verifies provenance and accounting. It cannot establish source truth,
admission eligibility, dependency semantics, or model performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROTOCOL_SHA256 = "de8469844bde89b97622c24778d392a66a7a2762f01536ee246515784abadab9"
REGISTER = "data/correction_gate_v11/screen_register.json"
DOMAINS = ("corporate", "science", "technical")
LIMITS = {
    "queries_per_domain": 3,
    "new_histories_per_domain": 2,
    "new_histories_total": 6,
    "authored_documents_per_history": 4,
}
GUARDRAILS = {
    "access_failure_implies_substantive_ineligibility": False,
    "screen_shortfall_implies_population_rarity": False,
    "discovery_implies_method_gain": False,
    "current_document_diagnostic_is_paired_history": False,
    "current_document_diagnostic_is_core_admission": False,
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def local_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Expected project-relative path: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path leaves project: {relative}")
    return resolved


def read_json(root: Path, relative: str) -> Any:
    return json.loads(local_path(root, relative).read_text(encoding="utf-8"))


def records(domain: str, discovery: dict[str, Any]) -> list[dict[str, Any]]:
    return discovery["histories" if domain == "science" else "candidates"]


def record_id(domain: str, record: dict[str, Any]) -> str:
    return record["history_id" if domain == "science" else "id"]


def normalized_budget(domain: str, discovery: dict[str, Any]) -> dict[str, Any]:
    if domain == "technical":
        budget = discovery["budget_used"]
        queries = budget["queries"]
        histories = budget["new_histories"]
        query_limit = LIMITS["queries_per_domain"]
        history_limit = LIMITS["new_histories_per_domain"]
        counts = {r["id"]: len(r["documents"]) for r in records(domain, discovery)}
    else:
        budget = discovery["budget"]
        queries = budget["queries_used"]
        histories = budget["new_histories_inspected"]
        query_limit = budget["queries_limit"]
        history_limit = budget["new_histories_limit"]
        count_key = (
            "authored_document_representations_attempted"
            if domain == "corporate"
            else "documents_by_history"
        )
        counts = budget[count_key]
    return {
        "queries_used": queries,
        "queries_limit": query_limit,
        "histories_inspected": histories,
        "histories_limit": history_limit,
        "document_targets_by_history": counts,
        "document_targets_limit_per_history": LIMITS["authored_documents_per_history"],
    }


def verify(root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    bindings_checked: dict[str, str] = {}

    def check(name: str, condition: bool, detail: Any = None) -> None:
        entry: dict[str, Any] = {"name": name, "passed": bool(condition)}
        if detail is not None:
            entry["detail"] = detail
        checks.append(entry)

    def bound(binding: dict[str, Any], label: str, expected_path: str) -> Any:
        check(f"{label}: expected path", binding["path"] == expected_path)
        path = local_path(root, binding["path"])
        actual = sha256(path.read_bytes())
        bindings_checked[binding["path"]] = actual
        check(f"{label}: file hash", actual == binding["sha256"])
        return json.loads(path.read_text(encoding="utf-8"))

    register = read_json(root, REGISTER)
    check("register schema", register["schema_version"] == "correction_expansion_screen_register_v0.11")
    inputs = register["input_bindings"]
    protocol = bound(inputs["protocol"], "protocol", "data/correction_gate_v11/expansion_protocol.json")
    check("frozen protocol hash", inputs["protocol"]["sha256"] == PROTOCOL_SHA256)
    bound(inputs["parent_route"], "parent route", "data/provenance/route_decision_v10.json")
    check("protocol parent route binding", protocol["parent_route_sha256"] == inputs["parent_route"]["sha256"])
    previous = bound(inputs["v10_screen_register"], "previous screen", "data/correction_gate_v10/screen_register.json")
    check("frozen protocol limits", protocol["limits"] == LIMITS)
    check("protocol budget snapshot", register["protocol_budget_snapshot"] == protocol["limits"])
    check("protocol budget hash", register["protocol_budget_sha256"] == sha256(canonical(protocol["limits"])))
    check("protocol domain set", set(protocol["domains"]) == set(DOMAINS))
    check("three discovery bindings", set(inputs["discoveries"]) == set(DOMAINS))
    check("three domain budgets", set(register["per_domain_budgets"]) == set(DOMAINS))
    check("three domain budget hashes", set(register["per_domain_budget_sha256"]) == set(DOMAINS))

    rows = register["histories"]
    ids = [row["id"] for row in rows]
    previous_ids = {row["id"] for row in previous["histories"]}
    check("six unique new history IDs", len(ids) == 6 and len(set(ids)) == 6)
    check("new IDs disjoint from v10", not (set(ids) & previous_ids))
    check("only declared domains", all(row["domain"] in DOMAINS for row in rows))
    total_queries = 0
    total_histories = 0
    domain_counts: dict[str, Any] = {}
    for domain in DOMAINS:
        expected_path = f"data/correction_gate_v11/discovery_{domain}.json"
        source_binding = inputs["discoveries"][domain]
        discovery = bound(source_binding, f"{domain} discovery", expected_path)
        declared_protocol_path = discovery.get("protocol", discovery.get("protocol_path"))
        check(f"{domain}: protocol path", declared_protocol_path == inputs["protocol"]["path"])
        if "protocol_sha256" in discovery:
            check(f"{domain}: declared protocol hash", discovery["protocol_sha256"] == PROTOCOL_SHA256)
        query_key = {"corporate": "search_log", "science": "queries", "technical": "queries_executed"}[domain]
        source_records = records(domain, discovery)
        source_ids = [record_id(domain, row) for row in source_records]
        budget = normalized_budget(domain, discovery)
        check(f"{domain}: budget projection", register["per_domain_budgets"][domain] == budget)
        check(f"{domain}: budget hash", register["per_domain_budget_sha256"][domain] == sha256(canonical(budget)))
        check(f"{domain}: three executed queries", len(discovery[query_key]) == budget["queries_used"] == 3)
        check(f"{domain}: two inspected histories", len(source_records) == len(set(source_ids)) == budget["histories_inspected"] == 2)
        check(f"{domain}: query limit", budget["queries_limit"] == 3 and budget["queries_used"] <= budget["queries_limit"])
        check(f"{domain}: history limit", budget["histories_limit"] == 2 and budget["histories_inspected"] <= budget["histories_limit"])
        doc_counts = budget["document_targets_by_history"]
        check(f"{domain}: document accounting keys", set(doc_counts) == set(source_ids))
        check(f"{domain}: document target limits", all(type(n) is int and 0 <= n <= 4 for n in doc_counts.values()))
        domain_rows = [row for row in rows if row["domain"] == domain]
        check(f"{domain}: all source IDs preserved", {row["id"] for row in domain_rows} == set(source_ids) and len(domain_rows) == 2)
        for source_record in source_records:
            identity = record_id(domain, source_record)
            matching = [row for row in domain_rows if row["id"] == identity]
            if len(matching) != 1:
                continue
            row = matching[0]
            check(f"{identity}: full source-discovery record preserved", row["discovery_record"] == source_record)
            check(f"{identity}: record hash", row["discovery_record_sha256"] == sha256(canonical(source_record)))
            check(f"{identity}: file binding", row["discovery_file"] == expected_path and row["discovery_sha256"] == source_binding["sha256"])
            stratum = source_record.get("provisional_stratum", source_record.get("stratum"))
            check(f"{identity}: provisional stratum preserved", row["provisional_discovery_stratum"] == stratum)
            check(f"{identity}: no full/core admission", row["full_prefix_admitted"] is False and row["core_admitted"] is False)
            check(f"{identity}: diagnostic separation", row["current_document_diagnostic_candidate"] is (identity == "jama_opioid_marketing"))
        total_queries += budget["queries_used"]
        total_histories += budget["histories_inspected"]
        domain_counts[domain] = budget

    check("nine queries total", total_queries == register["queries_executed"] == 9)
    check("six histories total", total_histories == register["candidate_histories_screened"] == 6)
    check("zero full/core admissions", register["full_prefix_admitted"] == register["core_admitted"] == 0)
    check("target not quota", register["target_is_not_quota"] is True and protocol["target_is_not_quota"] is True)
    check("no access-failure or performance inference declared", register["claim_guardrails"] == GUARDRAILS)
    diagnostics = register["current_document_diagnostics"]
    check("JAMA current-document diagnostic listed separately", len(diagnostics) == 1 and diagnostics[0]["history_id"] == "jama_opioid_marketing")
    check("diagnostic has no historical or core admission", all(d["paired_history"] is False and d["core_admitted"] is False and d["discovery_only_in_this_register"] is True for d in diagnostics))
    check("diagnostic provenance limits retained", len(diagnostics[0]["preserved_limits"]) >= 6)
    return {
        "schema_version": "discovery_metadata_verification_v0.11",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": all(check["passed"] for check in checks),
        "metadata_only": True,
        "network_calls": 0,
        "model_forward_calls": 0,
        "register": {"path": REGISTER, "sha256": sha256(local_path(root, REGISTER).read_bytes())},
        "checker_sha256": sha256(Path(__file__).read_bytes()),
        "input_hashes": bindings_checked,
        "checks_count": len(checks),
        "checks": checks,
        "totals": {"queries": total_queries, "new_histories": total_histories, "full_prefix_admitted": 0, "core_admitted": 0},
        "per_domain": domain_counts,
        "limitations": [
            "This checks metadata provenance, accounting, and declared claim boundaries only.",
            "Preserving complete discovery records preserves reported URLs, failures, and qualifications; their source truth is not independently verified here.",
            "Guardrail declarations cannot machine-certify every natural-language inference. Access failures are not evidence of substantive ineligibility or empirical method failure.",
            "The JAMA current-document diagnostic is separate from historical paired-source admission and the current core.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", default="results/discovery_verification_v11.json")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        result = verify(root)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        result = {
            "schema_version": "discovery_metadata_verification_v0.11",
            "passed": False,
            "metadata_only": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
    output = local_path(root, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": result.get("checks_count", 0), "output": args.output}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Replay tiny authored candidate problems; never call these benchmark results."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.decoder import (  # noqa: E402
    Link, Mention, Penalty, Problem, Reading,
    decode_independent, decode_iterative, decode_joint, evaluate_assignment,
)
from temporal_state.models import Source  # noqa: E402


def reading(raw):
    raw = dict(raw)
    if raw.get("key") is not None:
        raw["key"] = tuple(raw["key"])
    for name in ("context_source_ids", "violations"):
        if name in raw:
            raw[name] = tuple(raw[name])
    return Reading(**raw)


def load_problem(raw):
    raw = dict(raw)
    raw["sources"] = tuple(Source(**s) for s in raw["sources"])
    mentions = []
    for m in raw["mentions"]:
        m = dict(m)
        m["readings"] = tuple(reading(r) for r in m["readings"])
        m["context_source_ids"] = tuple(m.get("context_source_ids", ()))
        mentions.append(Mention(**m))
    raw["mentions"] = tuple(mentions)
    links = []
    for link in raw["links"]:
        link = dict(link)
        for name in ("context_source_ids", "violations", "reference_span"):
            if link.get(name) is not None:
                link[name] = tuple(link[name])
        links.append(Link(**link))
    raw["links"] = tuple(links)
    raw["context_source_ids"] = tuple(raw.get("context_source_ids", ()))
    raw["penalties"] = tuple(Penalty(**p) for p in raw.get("penalties", ()))
    return Problem(**raw)


def main():
    path = ROOT / "data/diagnostics/decoder_fixtures.json"
    fixture_bytes = path.read_bytes()
    fixtures = json.loads(fixture_bytes)
    rows = []
    checks = 0
    for case in fixtures["cases"]:
        # Expected identities and scores remain outside the inference input.
        p = load_problem(case["problem"])
        methods = {
            "independent": decode_independent(p),
            "iterative": decode_iterative(p),
            "iterative_restarts_10": decode_iterative(p, restarts=10, seed=7),
            "joint_exact": decode_joint(p),
        }
        results = {}
        for method, result in methods.items():
            if result.objective != case["expected_objectives"][method]:
                raise AssertionError(f"Unexpected objective: {case['case_id']} / {method}")
            if evaluate_assignment(p, dict(result.reading_ids), dict(result.link_ids)) != result.objective:
                raise AssertionError("Returned state failed the shared evaluator")
            checks += 2
            results[method] = asdict(result)
            if "diagnostic_identity_labels" in case:
                selected = dict(result.reading_ids)
                results[method]["authored_identity_label_matches"] = sum(
                    selected[mid] == rid for mid, rid in case["diagnostic_identity_labels"].items())
                results[method]["authored_identity_label_count"] = len(case["diagnostic_identity_labels"])
        rows.append({"case_id": case["case_id"], "purpose": case["purpose"], "methods": results})
    output = {
        "schema_version": "0.1", "evidence_class": "synthetic_candidate_contract_checks",
        "fixture_sha256": sha256(fixture_bytes).hexdigest(),
        "paper_performance_claim": False,
        "limitations": [
            "Candidates, scores, and diagnostic identities are authored, not predicted from natural text.",
            "Objective improvement is not answer accuracy or identity correctness.",
            "The misleading-score control deliberately makes exact optimization prefer a wrong identity.",
            "Exhaustive enumeration is bounded and supplies no scalability evidence.",
            "Timings are single-process diagnostic observations, not matched-budget comparisons.",
        ],
        "case_count": len(rows), "passed_contract_checks": checks, "cases": rows,
    }
    output_path = ROOT / "data/diagnostics/decoder_results.json"
    output_path.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"evidence_class": output["evidence_class"], "cases": len(rows),
                      "passed_contract_checks": checks, "output": str(output_path)}, indent=2))


if __name__ == "__main__":
    main()

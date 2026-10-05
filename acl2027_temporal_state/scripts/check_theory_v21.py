#!/usr/bin/env python3
"""Run invented finite mechanism controls; no model or empirical QA calls."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.certificate_v21 import (  # noqa: E402
    Cost, CoverageAssertion, additive_cost, evaluate, problem_from_dict, solve_exact,
)


def run(fixture_path):
    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    if fixture["schema_version"] != 21:
        raise ValueError("expected theory control schema 21")
    problem = problem_from_dict(fixture["problem"])
    unit = "authored_cost_units"
    additive = additive_cost({a: 1 for a in problem.atoms}, unit=unit,
                             provenance="Authored unit-cost atoms, not tokens or measured compute.")

    def nonmonotone(selected, received):
        # This invented counter only witnesses the failure of monotone-cost
        # pruning. It is not a measurement of any tokenizer or renderer.
        return Cost(2 if "bridge" in received else len(received), unit,
                    "Authored nonmonotone counterexample, not native token measurements.")

    checks = []
    for case in fixture["evaluations"]:
        result = evaluate(problem, tuple(case["selected"]), additive)
        passed = (result.authorized == case["authorized"] and
                  result.local_certificate == case["local_certificate"] and
                  list(result.activated_witness_ids) == case["active"] and
                  ("blocker" not in case or case["blocker"] in result.blockers))
        checks.append({"name": case["name"], "passed": passed,
                       "authorized": result.authorized,
                       "local_certificate": result.local_certificate,
                       "activated_witness_ids": result.activated_witness_ids,
                       "blockers": result.blockers})
    for case in fixture["solutions"]:
        modes = {"additive": additive, "nonmonotone-authored": nonmonotone}
        if case["cost_mode"] not in modes:
            raise ValueError(f"unknown authored cost mode: {case['cost_mode']}")
        counter = modes[case["cost_mode"]]
        options = {} if "budget" not in case else {"budget": case["budget"], "budget_unit": unit}
        result = solve_exact(problem, counter, **options)
        best = result.best
        passed = (result.status == "OPTIMAL" and result.evaluated_subsets == 32 and
                  best is not None and str(best.cost.amount) == case["cost"] and
                  list(best.selected_action_ids) == case["selected"] and best.authorized)
        checks.append({"name": case["name"], "passed": passed,
                       "status": result.status, "objective": result.objective,
                       "evaluated_subsets": result.evaluated_subsets,
                       "selected_action_ids": None if best is None else best.selected_action_ids,
                       "cost": None if best is None else str(best.cost.amount),
                       "cost_unit": unit, "scope": result.scope})
    unknown = replace(problem, coverage=CoverageAssertion(False, "Authored unknown gate left unresolved."))
    authorized = solve_exact(unknown, additive)
    local = solve_exact(unknown, additive, require_coverage=False)
    checks.append({"name": "local-optimum-cannot-clear-unknown",
                   "passed": (authorized.status == "INFEASIBLE" and
                              authorized.objective == "authorized_certificate" and
                              local.status == "OPTIMAL" and local.objective == "local_certificate" and
                              local.best is not None and not local.best.authorized),
                   "authorized_objective_status": authorized.status,
                   "local_objective_status": local.status,
                   "local_optimum_authorized": None if local.best is None else local.best.authorized})
    artifacts = [fixture_path, ROOT / "src/temporal_state/certificate_v21.py", Path(__file__)]
    def artifact_name(path):
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)
    return {"schema_version": 21, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "evidence_status": fixture["evidence_status"],
            "scope": "Finite supplied-map algebra checks; not source authentication, semantic validation, model QA, or retrieval evaluation.",
            "checks_passed": sum(c["passed"] for c in checks), "checks_total": len(checks),
            "all_passed": all(c["passed"] for c in checks), "checks": checks,
            "artifact_sha256": {artifact_name(p): sha256(p.read_bytes()).hexdigest()
                                for p in artifacts}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "data/theory_controls_v21.json")
    parser.add_argument("--output", type=Path, help="Save a new receipt; refuses to overwrite an existing file.")
    args = parser.parse_args()
    receipt = run(args.fixture.resolve())
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
        print(f"{receipt['checks_passed']}/{receipt['checks_total']} authored checks passed; receipt: {args.output}")
    else:
        print(rendered, end="")
    return 0 if receipt["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

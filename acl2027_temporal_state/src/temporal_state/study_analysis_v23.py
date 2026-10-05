"""Frozen v23 paired issuer-history analysis; no model calls or semantic grading.

The input is an independently graded, complete intention-to-run table. Statistical
criteria passing is not source/reference/protocol admission. See the v23 study
protocol for assumptions and the required run freeze.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

HISTORIES = 200
ITEMS_PER_HISTORY = 6
ALPHA = 0.05
MINIMUM_GAIN = Fraction(1, 20)
MAX_HISTORY_RISK = 0.05
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 230519
ARMS = ("proposed", "comparator")


def clopper_pearson_upper(errors: int, trials: int, alpha: float = ALPHA) -> float:
    """One-sided exact binomial upper bound under iid Bernoulli histories."""
    if (type(errors) is not int or type(trials) is not int or trials < 1
            or not 0 <= errors <= trials or not 0 < alpha < 1):
        raise ValueError("need integer 0 <= errors <= trials and 0 < alpha < 1")
    if errors == trials:
        return 1.0
    if errors == 0:
        return -math.expm1(math.log(alpha) / trials)
    from scipy.stats import beta
    return float(beta.ppf(1 - alpha, errors + 1, trials - errors))


def paired_history_interval(differences: list[float]) -> dict:
    """Primary approximate t lower bound and prespecified bootstrap sensitivity.

    Histories, not questions, are iid units. The t bound is not a finite-sample
    distribution-free bound for these bounded, discrete differences. A zero
    sample variance blocks inferential success rather than inventing precision.
    This float-based interval helper does not decide the practical-effect gate;
    analyze_rows computes that decision from exact supported-answer counts.
    """
    if len(differences) < 2 or any(
            type(x) not in (int, float) or not math.isfinite(x) or not -1 <= x <= 1
            for x in differences):
        raise ValueError("need at least two finite history differences in [-1,1]")
    from scipy.stats import t
    n = len(differences)
    mean = statistics.fmean(differences)
    sd = statistics.stdev(differences)
    lower = None if sd == 0 else mean - float(t.ppf(1 - ALPHA, n - 1)) * sd / math.sqrt(n)
    rng = random.Random(BOOTSTRAP_SEED)
    boot = sorted(statistics.fmean(differences[rng.randrange(n)] for _ in range(n))
                  for _ in range(BOOTSTRAP_REPLICATES))
    # A fixed order-statistic percentile; never substituted for the primary t CI.
    quantile_index = math.ceil(ALPHA * BOOTSTRAP_REPLICATES) - 1
    return {
        "histories": n, "mean_gain": mean, "history_sd": sd,
        "one_sided_95pct_t_lower": lower,
        "t_interval_status": "zero_variance_inference_blocked" if sd == 0 else "approximate",
        "one_sided_95pct_bootstrap_lower_sensitivity_only": boot[quantile_index],
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "superiority_pass": lower is not None and lower > 0,
    }


def _validate_outcome(outcome: dict) -> None:
    if not isinstance(outcome, dict):
        raise ValueError("each arm needs an outcome object")
    allowed = {"emitted", "joint_correct", "unsupported", "output_audited", "status"}
    if set(outcome) != allowed:
        raise ValueError("outcome must have exactly the five documented fields")
    for key in ("emitted", "joint_correct", "unsupported", "output_audited"):
        if type(outcome.get(key)) is not bool:
            raise ValueError(f"{key} must be a boolean, never a truthy string")
    if outcome.get("status") not in ("completed", "technical_failure", "unattempted"):
        raise ValueError("unknown execution status")
    if outcome["status"] == "unattempted" and outcome["emitted"]:
        raise ValueError("an unattempted item cannot have a substantive emission")
    if outcome["joint_correct"] and (
            not outcome["emitted"] or outcome["unsupported"]
            or outcome["status"] != "completed"):
        raise ValueError("joint correctness requires a completed, supported emission")
    if outcome["unsupported"] and not outcome["emitted"]:
        raise ValueError("unsupported emission requires emitted=True")
    if not outcome["output_audited"] and outcome["joint_correct"]:
        raise ValueError("unaudited output cannot be scored correct")


def analyze_rows(records: list[dict]) -> dict:
    """Analyze exactly 200 issuer histories, each with all six paired items.

    Any unaudited output history is conservatively counted unsafe for the risk
    gate, including lost technical-failure outputs; observed risk stays separate.
    The caller must authenticate sources, references, schedule and grades.
    """
    if not isinstance(records, list) or len(records) != HISTORIES * ITEMS_PER_HISTORY:
        raise ValueError("the fixed schedule requires exactly 1200 paired item rows")
    groups: dict[str, list[dict]] = defaultdict(list)
    history_issuers: dict[str, str] = {}
    seen_items: set[tuple[str, str]] = set()
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("item row must be an object")
        if set(row) != {"history_id", "issuer_id", "item_id", *ARMS}:
            raise ValueError("item row must have exactly the five documented fields")
        for key in ("history_id", "issuer_id", "item_id"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"missing nonempty {key}")
        h, issuer, item = row["history_id"], row["issuer_id"], row["item_id"]
        if (h, item) in seen_items:
            raise ValueError("duplicate item within a history")
        seen_items.add((h, item))
        if h in history_issuers and history_issuers[h] != issuer:
            raise ValueError("one history cannot have multiple issuer IDs")
        history_issuers[h] = issuer
        for arm in ARMS:
            _validate_outcome(row.get(arm))
        groups[h].append(row)
    if len(groups) != HISTORIES or any(len(rows) != ITEMS_PER_HISTORY for rows in groups.values()):
        raise ValueError("need exactly 200 histories with six scheduled items each")
    if len(set(history_issuers.values())) != HISTORIES:
        raise ValueError("repeated issuer histories are not independent sampling units")

    per_history = []
    for h, rows in sorted(groups.items()):
        entry = {"history_id": h, "issuer_id": history_issuers[h]}
        for arm in ARMS:
            outcomes = [row[arm] for row in rows]
            entry[arm] = {
                "supported_answer_yield": sum(o["joint_correct"] for o in outcomes) / ITEMS_PER_HISTORY,
                "any_observed_unsupported_emission": any(o["unsupported"] for o in outcomes),
                "unsafe_for_gate": any(o["unsupported"] or not o["output_audited"] for o in outcomes),
                "emissions": sum(o["emitted"] for o in outcomes),
                "wrong_joint_answers": sum(o["emitted"] and not o["joint_correct"] for o in outcomes),
                "unsupported_emissions": sum(o["unsupported"] for o in outcomes),
                "technical_failures": sum(o["status"] == "technical_failure" for o in outcomes),
                "unattempted": sum(o["status"] == "unattempted" for o in outcomes),
                "unaudited": sum(not o["output_audited"] for o in outcomes),
            }
        entry["yield_difference"] = (entry["proposed"]["supported_answer_yield"]
                                     - entry["comparator"]["supported_answer_yield"])
        per_history.append(entry)
    effect = paired_history_interval([h["yield_difference"] for h in per_history])
    # Six scheduled items in every history make the equal-history mean identical
    # to the exact difference in integer correct-answer counts divided by 1200.
    # Do not compare a sum of rounded 4/6 - 3/6 differences to the 5% boundary.
    net_supported_answers = sum(
        int(row["proposed"]["joint_correct"]) - int(row["comparator"]["joint_correct"])
        for row in records)
    exact_gain = Fraction(net_supported_answers, len(records))
    effect.update({
        "mean_gain": float(exact_gain),
        "mean_gain_exact": str(exact_gain),
        "net_supported_answers": net_supported_answers,
        "practical_gain_pass": exact_gain >= MINIMUM_GAIN,
    })
    arm_results = {}
    for arm in ARMS:
        errors = sum(h[arm]["unsafe_for_gate"] for h in per_history)
        upper = clopper_pearson_upper(errors, HISTORIES)
        emissions = sum(h[arm]["emissions"] for h in per_history)
        wrong = sum(h[arm]["wrong_joint_answers"] for h in per_history)
        unsupported = sum(h[arm]["unsupported_emissions"] for h in per_history)
        arm_results[arm] = {
            "mean_supported_answer_yield": statistics.fmean(h[arm]["supported_answer_yield"] for h in per_history),
            "unsafe_histories_for_gate": errors,
            "observed_unsupported_histories": sum(h[arm]["any_observed_unsupported_emission"] for h in per_history),
            "one_sided_95pct_history_risk_upper": upper,
            "history_risk_pass": upper <= MAX_HISTORY_RISK,
            "emissions": emissions,
            "answer_rate": emissions / len(records),
            "joint_error_mass": wrong / len(records),
            "selective_joint_error_point_estimate": wrong / emissions if emissions else None,
            "unsupported_emission_mass": unsupported / len(records),
            "selective_unsupported_point_estimate": unsupported / emissions if emissions else None,
            "technical_failures": sum(h[arm]["technical_failures"] for h in per_history),
            "unattempted": sum(h[arm]["unattempted"] for h in per_history),
            "unaudited_outputs": sum(h[arm]["unaudited"] for h in per_history),
        }
    gates = [effect["practical_gain_pass"], effect["superiority_pass"]]
    gates.extend(arm_results[a]["history_risk_pass"] for a in ARMS)
    import scipy
    return {
        "schema_version": "study_analysis_v23", "rows": len(records),
        "statistical_criteria_pass": all(gates),
        "claim_admission": "not_determined_by_statistics_requires_sealed_run_and_independent_reference_review",
        "effect": effect, "arms": arm_results, "per_history": per_history,
        "limits": [
            "Primary t inference is approximate and assumes independent exchangeable issuer histories.",
            "History risk is any unsupported emission among six scheduled items, not risk conditional on answering.",
            "Five points is the observed practical threshold, not a five-point confidence lower bound.",
            "Unknown/lost outputs are conservatively unsafe for the risk gate.",
            "Input grades, semantic truth, exposure exclusions and cost compliance are not validated here.",
        ],
        "analysis_runtime": {"scipy": scipy.__version__},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="sealed grade JSON containing a records list")
    parser.add_argument("--output", type=Path, required=True, help="new output file, never overwritten")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    payload = json.loads(raw)
    result = analyze_rows(payload["records"])
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    result["analysis_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    main()

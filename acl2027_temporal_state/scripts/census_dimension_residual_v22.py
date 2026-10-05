"""Dimension-residual policy census. Counts and axis names only; not natural QA."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from temporal_state.typed_binding_v22 import dimension_residual_profile
from temporal_state.typed_reader_v15_1 import ReaderError, read_inline_xbrl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-directory", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output path already exists")
    protocol = json.loads(args.protocol.read_text())
    profiles, failures = [], []
    for item in protocol["files"]:
        source = (args.input_directory / item["external_filename"]).read_bytes()
        if hashlib.sha256(source).hexdigest() != item["expected_sha256"] or len(source) != item["expected_bytes"]:
            failures.append({"filename": item["external_filename"], "reason": "hash_or_size_mismatch"})
            continue
        try:
            document = read_inline_xbrl(source, source_version=item["external_filename"])
        except ReaderError as exc:
            failures.append({"filename": item["external_filename"], "reason": str(exc)})
            continue
        profile = dimension_residual_profile(document, source_version=item["external_filename"])
        profile["source_version"] = item["external_filename"]
        profiles.append(profile)
    policies: dict = {}
    axes: dict = {}
    buckets = {"2": 0, "3-5": 0, "6-10": 0, "11+": 0}
    for profile in profiles:
        for label, count in profile["policy_outcomes"].items():
            policies[label] = policies.get(label, 0) + count
        for bucket, count in profile["dimension_setting_buckets"].items():
            buckets[bucket] += count
        for row in profile["differing_axes"]:
            axes[row["axes"]] = axes.get(row["axes"], 0) + row["groups"]
    ranked = sorted(axes.items(), key=lambda item: (-item[1], item[0]))[:20]
    totals = {
        "documents_profiled": len(profiles),
        "documents_failed": len(failures),
        "same_dimension_value_conflicts": sum(row["same_dimension_value_conflicts"] for row in profiles),
        "dimension_value_changing_groups": sum(row["dimension_value_changing_groups"] for row in profiles),
        "empty_and_explicit_dimension_groups": sum(row["empty_and_explicit_dimension_groups"] for row in profiles),
        "dimension_setting_buckets": buckets,
        "policy_outcomes": policies,
        "differing_axes": [{"axes": name, "groups": count} for name, count in ranked],
        "natural_questions": 0,
        "admitted_answers": 0,
        "development_probes": sum(len(row["probes"]) for row in profiles),
        "held_out_probes": 0,
    }
    payload = {
        "schema": "dimension_residual_v22",
        "evidence_status": (
            "Development diagnostic, not held out. Dimension groups keep concept, entity, period, and unit fixed. "
            "On these groups aspect filtering and the certificate both abstain, while sufficiency emits. "
            "The certificate does not beat typed filtering. Probe ids contain no values. Not natural QA."
        ),
        "profiles": profiles,
        "failures": failures,
        "totals": totals,
    }
    probe_ids = [probe["probe_id"] for profile in profiles for probe in profile["probes"]]
    if len(probe_ids) != len(set(probe_ids)):
        raise SystemExit("development probe ids collided")
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({key: totals[key] for key in totals if key != "differing_axes"}, sort_keys=True))
    for row in ranked[:8]:
        print(f"{row[1]:5d}  {row[0]}")


if __name__ == "__main__":
    main()

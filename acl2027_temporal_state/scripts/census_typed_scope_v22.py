"""Open-scope and taxonomy-year diagnostics. Counts only; no admitted join."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from temporal_state.typed_binding_v22 import namespace_year_profile, open_scope_profile
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
    parsed = {}
    failures = []
    profiles = []
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
        profile = open_scope_profile(document)
        profile["source_version"] = item["external_filename"]
        profiles.append(profile)
        parsed.setdefault(item["issuer_id"], []).append((item["external_filename"], document))
    aspects = ("entity", "period", "unit", "dimensions")
    opened = {}
    for aspect in aspects:
        opened[aspect] = {
            "value_changing_groups": sum(row["omitted_aspect"][aspect]["value_changing_groups"] for row in profiles),
            "same_value_different_scope_groups": sum(
                row["omitted_aspect"][aspect]["same_value_different_scope_groups"] for row in profiles),
            "unique_groups": sum(row["omitted_aspect"][aspect]["unique_groups"] for row in profiles),
        }
    pairs = []
    for issuer, versions in parsed.items():
        versions.sort(key=lambda item: item[0])
        if len(versions) != 2:
            failures.append({"issuer": issuer, "reason": "expected_two_versions", "found": len(versions)})
            continue
        (first_name, first), (second_name, second) = versions
        row = namespace_year_profile(first, second)
        row["issuer_id"] = issuer
        row["first"] = first_name
        row["second"] = second_name
        pairs.append(row)
    totals = {
        "documents_profiled": len(profiles),
        "documents_failed": len(failures),
        "omitted_aspect": opened,
        "year_token_only_bindings": sum(row["namespace_class"]["year_token_only"] for row in pairs),
        "calendar_token_only_bindings": sum(row["namespace_class"]["calendar_token_only"] for row in pairs),
        "other_namespace_bindings": sum(row["namespace_class"]["other_namespace"] for row in pairs),
        "identical_namespace_bindings": sum(row["namespace_class"]["identical_namespace"] for row in pairs),
        "year_token_only_not_same_value": sum(row["not_same_value"]["year_token_only"] for row in pairs),
        "calendar_token_only_not_same_value": sum(row["not_same_value"]["calendar_token_only"] for row in pairs),
        "other_namespace_not_same_value": sum(row["not_same_value"]["other_namespace"] for row in pairs),
        "admitted_cross_filing_joins": 0,
        "natural_questions": 0,
    }
    payload = {
        "schema": "typed_scope_probe_v22",
        "evidence_status": (
            "Development diagnostic. Open-scope counts are groups inside one filing. "
            "Namespace classes describe local-name overlaps and admit no cross-filing join. "
            "Not natural QA."
        ),
        "profiles": profiles,
        "pairs": pairs,
        "failures": failures,
        "totals": totals,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(totals, sort_keys=True))


if __name__ == "__main__":
    main()

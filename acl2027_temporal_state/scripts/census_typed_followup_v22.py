"""Source check after the typed census. Counts only; not a QA result.

Exact cross-filing overlap uses the reported concept URI. The local-name
comparison drops the URI and is recorded as a diagnostic, not a mapping.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from temporal_state.typed_binding_v22 import compare_filings, conflict_profile
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
        parsed.setdefault(item["issuer_id"], []).append((item["external_filename"], document))
        profile = conflict_profile(document)
        profile["source_version"] = item["external_filename"]
        profiles.append(profile)
    pairs = []
    for issuer, versions in parsed.items():
        versions.sort(key=lambda item: item[0])
        if len(versions) != 2:
            failures.append({"issuer": issuer, "reason": "expected_two_versions", "found": len(versions)})
            continue
        (first_name, first), (second_name, second) = versions
        for mode in ("exact", "local_name_diagnostic"):
            row = compare_filings(first, second, mode)
            row["issuer_id"] = issuer
            row["first"] = first_name
            row["second"] = second_name
            pairs.append(row)
    totals = {
        "documents_profiled": len(profiles),
        "documents_failed": len(failures),
        "conflict_bindings": sum(row["conflict_bindings"] for row in profiles),
        "conflict_concepts": sum(row["conflict_concepts"] for row in profiles),
        "within_5pct_or_closer": sum(
            row["ratio_buckets"]["within_0.1pct"] + row["ratio_buckets"]["within_1pct"]
            + row["ratio_buckets"]["within_5pct"] for row in profiles),
        "larger_conflicts": sum(row["ratio_buckets"]["larger"] for row in profiles),
        "exact_shared_bindings": sum(row["shared"] for row in pairs if row["mode"] == "exact"),
        "local_name_shared_bindings": sum(row["shared"] for row in pairs if row["mode"] == "local_name_diagnostic"),
        "local_name_not_same_value": sum(row["not_same_value"] for row in pairs if row["mode"] == "local_name_diagnostic"),
        "natural_questions": 0,
        "admitted_cross_filing_joins": 0,
    }
    payload = {
        "schema": "typed_binding_followup_v22",
        "evidence_status": (
            "Development source check. Same-binding conflicts are ratio buckets only. "
            "Exact cross-filing overlap keeps the concept URI. The local-name comparison "
            "drops taxonomy namespaces and is not an admitted join. Not natural QA."
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

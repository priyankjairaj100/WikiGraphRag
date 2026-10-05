#!/usr/bin/env python3
"""Post-audit accuracy and lexical-arithmetic checks; never changes frozen audit."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import argparse
import json
from pathlib import Path
import sys

import audit_sources_v23 as audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = json.loads(audit.RESULT.read_text())
    rows = []
    arithmetic_checks = 0
    cache_inputs = {}
    for doc in result["documents"]:
        path = args.external_dir / (doc["filename"] + ".parsed.json")
        cache_inputs[doc["filename"]] = audit.file_digest(path)
        parsed = json.loads(path.read_text())
        assert parsed["source_sha256"] == doc["source_sha256"]
        indexed = {f["fact_ordinal"]: f for f in parsed["facts"]}
        for conflict in doc["conflicts"]:
            by_accuracy = defaultdict(set)
            for f in conflict["facts"]:
                by_accuracy[f["accuracy"]["decimals"]].add(f["normalized_value"])
                original = indexed[f["fact_ordinal"]]
                assert original["normalized_value"] == f["normalized_value"]
                # Check supported dot-decimal or untransformed decimal
                # occurrences directly from lexical, sign and scale metadata.
                fmt = original["format_qname"]
                assert fmt is None or fmt.split("}")[-1] in ("numdotdecimal", "num-dot-decimal")
                cleaned = "".join(original["lexical_text"].replace(",", "").split())
                with localcontext() as ctx:
                    ctx.prec = 12000
                    expected = Decimal(cleaned) * (Decimal(10) ** original["scale_property"])
                    if original["attributes"].get("sign") == "-":
                        expected = -expected
                    assert expected == Decimal(original["normalized_value"])
                arithmetic_checks += 1
            assert None not in by_accuracy and "INF" not in by_accuracy
            most_precise = max(by_accuracy, key=int)
            rows.append({"probe_id": conflict["probe_id"],
                         "is_v22_representative_probe": conflict["is_v22_representative_probe"],
                         "unequal_values_at_equal_accuracy": any(len(v) > 1 for v in by_accuracy.values()),
                         "most_precise_decimals": most_precise,
                         "most_precise_has_unique_value": len(by_accuracy[most_precise]) == 1,
                         "table_and_prose_both_present": conflict["placement_kinds"] == ["prose", "table"],
                         "one_context_id": not conflict["context_ids_vary"],
                         "one_unit_id": len({f["unit_id"] for f in conflict["facts"]}) == 1})
    primary = [r for r in rows if r["is_v22_representative_probe"]]
    value = {"schema": "source_audit_checks_v23", "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "status": "post_audit_additional_checks_not_preregistered_method_evaluation",
             "script_sha256": audit.file_digest(__file__), "command_argv": sys.argv,
             "audit_result_sha256": audit.file_digest(audit.RESULT), "parsed_cache_sha256": cache_inputs,
             "guidance_url": "https://www.xbrl.org/WGN/xbrl-duplicates/WGN-2025-01-14/xbrl-duplicates-2025-01-14.html",
             "guidance_section": "3.3: equal-accuracy unequal values remain inconsistent; 9: highest-accuracy handling",
             "totals": {"conflict_groups_checked": len(rows), "primary_groups_checked": len(primary),
                        "unequal_values_at_equal_accuracy": sum(r["unequal_values_at_equal_accuracy"] for r in rows),
                        "most_precise_unique": sum(r["most_precise_has_unique_value"] for r in rows),
                        "both_table_and_prose": sum(r["table_and_prose_both_present"] for r in rows),
                        "one_context_id": sum(r["one_context_id"] for r in rows),
                        "one_unit_id": sum(r["one_unit_id"] for r in rows),
                        "lexical_sign_scale_arithmetic_checks": arithmetic_checks,
                        "arithmetic_mismatches": 0}, "rows": rows,
             "limits": ["Not an independent parser or taxonomy validation.",
                        "No source task, natural QA score or model gain is admitted.",
                        "Supplementary checks preserve the frozen initial screen and clarify its interpretation."]}
    audit.write_new(args.output, value)
    print(json.dumps(value["totals"], sort_keys=True))


if __name__ == "__main__":
    main()

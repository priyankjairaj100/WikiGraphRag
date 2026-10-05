"""Development census of typed binding disagreements. Not a QA evaluation.

Reads hashed filing bytes from a directory outside this repository. The JSON
result stores counts only. Source text and normalized values are not written.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from temporal_state.typed_binding_v22 import summarize_document
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
    documents, failures = [], []
    for item in protocol["files"]:
        path = args.input_directory / item["external_filename"]
        source = path.read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if digest != item["expected_sha256"] or len(source) != item["expected_bytes"]:
            failures.append({"filename": item["external_filename"], "reason": "hash_or_size_mismatch"})
            continue
        try:
            parsed = read_inline_xbrl(source, source_version=item["external_filename"])
        except ReaderError as exc:
            failures.append({"filename": item["external_filename"], "reason": str(exc)})
            continue
        if parsed["source_sha256"] != digest:
            failures.append({"filename": item["external_filename"], "reason": "reader_digest_mismatch"})
            continue
        documents.append(summarize_document(parsed, source_version=item["external_filename"]))
    totals = {
        "documents_read": len(documents),
        "documents_failed": len(failures),
        "eligible_facts": sum(row["eligible_facts"] for row in documents),
        "multi_class_concepts": sum(row["multi_class_concepts"] for row in documents),
        "unique_binding_concepts": sum(row["unique_binding_concepts"] for row in documents),
        "underspecified_residual": sum(row["underspecified_residual"] for row in documents),
        "fully_specified_checks": sum(row["fully_specified_checks"] for row in documents),
        "fully_specified_all_policies_agree": sum(row["fully_specified_all_policies_agree"] for row in documents),
        "same_binding_value_conflicts": sum(row["same_binding_value_conflicts"] for row in documents),
        "natural_questions": 0,
        "held_out_questions": 0,
    }
    payload = {
        "schema": "typed_binding_census_v22",
        "evidence_status": (
            "Development structural census of the twelve previously inspected filings. "
            "An underspecified probe names only the concept. A fully specified probe names "
            "one observed fact's complete binding. Not natural QA, not held-out, and not a method-superiority claim."
        ),
        "input_directory_recorded": False,
        "documents": documents,
        "failures": failures,
        "totals": totals,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(totals, sort_keys=True))


if __name__ == "__main__":
    main()

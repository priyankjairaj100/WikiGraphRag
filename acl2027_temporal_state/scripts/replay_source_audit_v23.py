#!/usr/bin/env python3
"""Portable exact-source replay preserving every frozen v23 artifact.

Usage: python3 scripts/replay_source_audit_v23.py --external-dir /path/to/html
       --output /path/outside/repository/source_audit_replay.json
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import audit_sources_v23 as audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-parent", type=Path)
    args = parser.parse_args()
    source_dir, output = args.external_dir.resolve(), args.output.resolve()
    if output.exists():
        raise SystemExit("Refusing to replace an existing output")
    if audit.ROOT.parent == output or audit.ROOT.parent in output.parents:
        raise SystemExit("Replay outputs must be outside the repository")
    protocol = json.loads(audit.PROTOCOL.read_text())
    verified = []
    for item in protocol["files"]:
        path = source_dir / item["external_filename"]
        if path.stat().st_size != item["expected_bytes"] or audit.file_digest(path) != item["expected_sha256"]:
            raise SystemExit("Source hash or size mismatch: " + item["external_filename"])
        verified.append(path)
    workspace = Path(tempfile.mkdtemp(prefix="wikigraph_source_audit_replay_", dir=args.work_parent))
    for path in verified:
        (workspace / path.name).symlink_to(path)
    audit.EXTERNAL, audit.RESULT = workspace, output
    audit.run()
    value = json.loads(output.read_text())
    original = json.loads((audit.ROOT / "results/source_audit_v23.json").read_text())
    keys = ("documents", "cross_filing_pairs", "totals")
    matched = all(value[k] == original[k] for k in keys)
    value["replay"] = {
        "wrapper_sha256": audit.file_digest(__file__), "command_argv": sys.argv,
        "original_receipt_sha256": audit.file_digest(audit.ROOT / "results/source_audit_v23.json"),
        "semantic_sections_match_original": matched, "compared_sections": list(keys),
        "source_files_verified": len(verified), "external_workspace": str(workspace),
        "frozen_artifacts_modified": False,
    }
    # Only the newly created caller-selected output is augmented.
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"replay_matches_original": matched, "output_sha256": audit.file_digest(output)}))
    if not matched:
        raise SystemExit("Replay differs from the historical result; preserve receipt and investigate")


if __name__ == "__main__":
    main()

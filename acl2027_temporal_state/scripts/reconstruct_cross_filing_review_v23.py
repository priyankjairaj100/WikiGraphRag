#!/usr/bin/env python3
"""Reconstruct external-only audit excerpts from public byte/hash locators.

Nothing is fetched. Requires the four exact recovered source files. Existing
files are verified rather than overwritten. This makes the explanatory audit
replay independent of transient inspection caches without publishing excerpts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from temporal_state.typed_reader_v15_1 import _parse


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(external):
    receipt = json.loads((ROOT / "results/cross_filing_explanatory_probe_v23.json").read_text())
    nodes, bodies, rebuilt = {}, {}, {}
    for filename, metadata in receipt["source_records"].items():
        source = (external / filename).read_bytes()
        assert sha(source) == metadata["source_sha256"]
        bodies[filename] = source
        nodes[filename] = {n.start: n for n in _parse(source)}
    for item in receipt["reviewed_passage_locators"]:
        filename = item["filename"]
        start, stop = item["byte_start"], item["byte_stop"]
        assert sha(bodies[filename][start:stop]) == item["span_sha256"]
        node = nodes[filename][start]
        assert node.stop == stop
        text = " ".join(node.text().split())
        target = item["inspection_file"]
        if "immediate" in target:
            key = filename
            record = {"start": start, "stop": stop, "text": text, "kind": item["selection_reason"]}
        elif "passages" in target:
            key = "PFE" if filename.startswith("PFE") else "BRK-B"
            record = {"filename": filename, "start": start, "stop": stop,
                      "span_sha256": item["span_sha256"], "text": text,
                      "score": item["heuristic_search_score_not_coverage"],
                      "selection_reason": item["selection_reason"]}
        else:
            key = filename
            record = {"start": start, "stop": stop, "span_sha256": item["span_sha256"],
                      "text": text, "reason": item["selection_reason"]}
        rebuilt.setdefault(target, {}).setdefault(key, []).append(record)
    for target, content in rebuilt.items():
        body = json.dumps(content, indent=2).encode()
        assert sha(body) == receipt["external_inspection_input_hashes"][target], target
        path = external / target
        if path.exists():
            assert path.read_bytes() == body, path
        else:
            with path.open("xb") as handle:
                handle.write(body)
    print(json.dumps({"reconstructed_or_verified_external_inspection_files": len(rebuilt)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-dir", type=Path, required=True)
    run(parser.parse_args().external_dir)

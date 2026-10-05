#!/usr/bin/env python3
"""Separate exact-byte recovery; reuses v15 transport without changing v15 receipts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

import recover_fresh_sources_v15 as transport

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "data/empirical_v23/source_recovery_protocol.json"
RECEIPT = ROOT / "results/source_recovery_v23.json"
EXTERNAL = Path("/workspace/scratch/bdef663e3dfc/wikigraph_v23_external")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configure():
    transport.PROTOCOL = PROTOCOL
    transport.RECEIPT = RECEIPT
    transport.EXTERNAL = EXTERNAL


def freeze():
    previous = ROOT / "data/typed_reader_v15/source_recovery_protocol.json"
    value = json.loads(previous.read_text())
    value.update({
        "schema_version": "source_recovery_protocol_v23",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "New exact-byte recovery for source conflict audit after v22 history-preserving revert.",
        "external_directory": str(EXTERNAL),
        "script_sha256": sha(Path(transport.__file__)),
        "wrapper_script_sha256": sha(__file__),
        "input_bindings": {"data/typed_reader_v15/source_recovery_protocol.json": sha(previous),
                           "scripts/recover_sources_v23.py": sha(__file__)},
        "runtime": {"python": sys.version, "platform": platform.platform()},
        "scope": ["Twelve previously exposed development filings only; no held-out evaluation.",
                  "Exact source bytes and partial bodies remain outside the Git repository.",
                  "No model calls; source-audit extraction requires its separate frozen protocol.",
                  "Preserve historical filename versus fiscal-year discrepancies."],
    })
    EXTERNAL.mkdir(parents=True, exist_ok=True)
    if any(EXTERNAL.iterdir()):
        raise SystemExit("Recovery destination must be empty")
    transport.write_new(PROTOCOL, value)
    print(json.dumps({"protocol_sha256": sha(PROTOCOL), "files": len(value["files"])}))


def recover():
    # The imported function retains its historical schema label in its raw
    # receipt. This wrapper promotes the *new* receipt only after completion,
    # and records both exact script identities. No historical receipt changes.
    transport.recover()
    value = json.loads(RECEIPT.read_text())
    value.update({"schema_version": "source_recovery_v23", "attempt_id": "v23_recovery_attempt01",
                  "transport_schema": "source_recovery_v15", "wrapper_script_sha256": sha(__file__),
                  "command": "python3 scripts/recover_sources_v23.py recover"})
    transport.save_receipt(value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "recover", "verify"))
    args = parser.parse_args()
    configure()
    if args.action == "verify":
        transport.verify()
    else:
        globals()[args.action]()

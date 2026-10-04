#!/usr/bin/env python3
"""Recover the exact v14 twelve-object corpus after workspace maintenance.

This is a separately recorded recovery attempt, not the historical capture.
No document content is emitted or saved within the repository.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "data/typed_reader_v15/source_recovery_protocol.json"
RECEIPT = ROOT / "results/source_recovery_v15.json"
EXTERNAL = Path("/dev/shm/wikigraph_v15/external/fresh_sources")
INPUTS = (
    "data/fresh_source_audit_v14/acquisition_protocol.json",
    "data/fresh_source_audit_v14/source_identity_structure.json",
    "results/fresh_source_integrity_reconciliation_v14.json",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def save_receipt(value):
    temporary = RECEIPT.with_suffix(".json.partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(RECEIPT)


def freeze():
    acquisition, identity, reconciliation = (
        json.loads((ROOT / item).read_text()) for item in INPUTS
    )
    identities = {x["source_path"]: x for x in identity["records"]}
    reconciled = {x["source_path"]: x for x in reconciliation["records"]}
    files = []
    for group in acquisition["selected"]:
        for item in group["files"]:
            old = identities[item["path"]]
            checked = reconciled[item["path"]]
            assert old["source_sha256"] == checked["source_sha256"]
            assert checked["verified_exact_released_object"] is True
            assert old["bytes"] == item["size"]
            assert item["size"] < 20 * 1024 * 1024
            assert Path(item["path"]).name == item["path"].split("/")[-1]
            files.append({
                "download_order": item["download_order"],
                "issuer_id": group["issuer_id"],
                "source_path": item["path"],
                "source_url": item["source_url"],
                "expected_sha256": old["source_sha256"],
                "expected_bytes": old["bytes"],
                "external_filename": Path(item["path"]).name,
                "historical_digest_namespace": checked["digest_namespace"],
                "historical_expected_object_digest": checked["expected_object_digest"],
                "actual_document_fiscal_year_values": old["document_fiscal_year_values"],
                "filename_matches_document_fiscal_year": old["filename_matches_document_fiscal_year"],
            })
    files.sort(key=lambda item: item["download_order"])
    assert len(files) == 12 and len({x["source_path"] for x in files}) == 12
    assert [x["download_order"] for x in files] == list(range(1, 13))
    assert sum(x["expected_bytes"] for x in files) == 62730877
    assert not any(EXTERNAL.iterdir()), "Recovery destination must be empty before freeze"
    value = {
        "schema_version": "source_recovery_protocol_v15",
        "frozen_at_utc": now(),
        "purpose": "New exact-byte recovery attempt after automated workspace maintenance removed external v14 filing bodies; enable the separately scoped typed-reader development stage.",
        "historical_capture_preserved": True,
        "changes_to_source_selection": 0,
        "dataset_repo": acquisition["dataset_repo"],
        "revision": acquisition["revision"],
        "input_bindings": {item: digest(ROOT / item) for item in INPUTS},
        "script_sha256": digest(Path(__file__)),
        "external_directory": str(EXTERNAL),
        "files": files,
        "policy": {
            "logical_attempts_per_file": 1,
            "redirects_allowed": True,
            "timeout_seconds_per_network_operation": 30,
            "max_bytes_per_file": 20 * 1024 * 1024,
            "max_total_downloaded_body_bytes": 100 * 1024 * 1024,
            "replacement_on_failure": False,
            "retry_on_failure": False,
            "partial_bytes_and_digests_retained": True,
            "integrity": "Every recovered body must match the historical content SHA256 and byte count, including the two already-reconciled LFS objects.",
            "public_metadata_only": True,
        },
        "scope": [
            "No QA/reference access, question creation, model inference or model download in this recovery.",
            "The source cohort remains exposed development data, not held out.",
            "Source identity, extraction semantics and comparison sufficiency are not established by recovery.",
            "Preserve the Berkshire filename versus fiscal-year discrepancy; no replacement or silent relabelling.",
        ],
    }
    write_new(PROTOCOL, value)
    print(json.dumps({"protocol_sha256": digest(PROTOCOL), "files": len(files), "expected_bytes": 62730877}))


def check_bindings(protocol):
    assert protocol["script_sha256"] == digest(Path(__file__)), "Recovery script changed after freeze"
    for path, expected in protocol["input_bindings"].items():
        assert digest(ROOT / path) == expected, f"Historical input changed: {path}"


def recover():
    protocol = json.loads(PROTOCOL.read_text())
    check_bindings(protocol)
    assert not RECEIPT.exists(), "A recovery receipt exists; repeat attempts are forbidden"
    assert not any(EXTERNAL.iterdir()), "Recovery destination is not empty"
    result = {
        "schema_version": "source_recovery_v15",
        "attempt_kind": "new recovery after automated workspace pruning",
        "attempt_id": "recovery_attempt01",
        "started_at_utc": now(),
        "protocol_sha256": digest(PROTOCOL),
        "script_sha256": digest(Path(__file__)),
        "records": [],
        "status": "in_progress",
        "model_calls": 0,
        "new_questions_or_references": 0,
        "historical_capture_replaced": False,
    }
    write_new(RECEIPT, result)
    total_bytes = 0
    policy = protocol["policy"]
    for item in protocol["files"]:
        started = time.monotonic()
        row = {key: item[key] for key in ("download_order", "issuer_id", "source_path", "source_url", "expected_bytes", "expected_sha256")}
        row.update({"started_at_utc": now(), "logical_attempts": 0, "observed_bytes": 0, "status": "pending"})
        remaining = policy["max_total_downloaded_body_bytes"] - total_bytes
        cap = min(policy["max_bytes_per_file"], remaining)
        partial = EXTERNAL / (item["external_filename"] + ".partial")
        final = EXTERNAL / item["external_filename"]
        body_hash = hashlib.sha256()
        if cap <= 0:
            row["status"] = "skipped_total_byte_cap"
        else:
            row["logical_attempts"] = 1
            row["status"] = "started"
            result["records"].append(row)
            save_receipt(result)
            try:
                request = urllib.request.Request(item["source_url"], headers={"User-Agent": "WikiGraphRag-source-recovery-v15/1.0", "Accept-Encoding": "identity"})
                with urllib.request.urlopen(request, timeout=policy["timeout_seconds_per_network_operation"]) as response:
                    row["http_status"] = response.status
                    row["content_type"] = response.headers.get("Content-Type")
                    row["content_length"] = response.headers.get("Content-Length")
                    parsed = urllib.parse.urlsplit(response.geturl())
                    row["final_url_without_query_or_fragment"] = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                    if row["content_length"] and int(row["content_length"]) > cap:
                        row["status"] = "rejected_advertised_byte_cap"
                    else:
                        with partial.open("xb") as target:
                            while row["observed_bytes"] < cap:
                                chunk = response.read(min(1024 * 1024, cap - row["observed_bytes"]))
                                if not chunk:
                                    break
                                target.write(chunk)
                                body_hash.update(chunk)
                                row["observed_bytes"] += len(chunk)
                                total_bytes += len(chunk)
                        row["observed_sha256"] = body_hash.hexdigest()
                        row["bytes_match"] = row["observed_bytes"] == item["expected_bytes"]
                        row["sha256_match"] = row["observed_sha256"] == item["expected_sha256"]
                        if row["http_status"] == 200 and row["bytes_match"] and row["sha256_match"]:
                            partial.rename(final)
                            row["status"] = "recovered_exact_historical_bytes"
                            row["external_path"] = str(final)
                        else:
                            row["status"] = "body_cap_reached" if row["observed_bytes"] == cap else "integrity_mismatch"
                            row["external_partial_path"] = str(partial)
            except Exception as error:
                row["status"] = "failed"
                # Avoid logging signed redirect query strings or remote response bodies.
                row["error_type"] = type(error).__name__
                if isinstance(error, urllib.error.HTTPError):
                    row["http_status"] = error.code
                if partial.exists():
                    row["observed_bytes"] = partial.stat().st_size
                    row["observed_sha256"] = digest(partial)
                    row["external_partial_path"] = str(partial)
        if row not in result["records"]:
            result["records"].append(row)
        row["finished_at_utc"] = now()
        row["elapsed_seconds"] = time.monotonic() - started
        row.setdefault("observed_sha256", body_hash.hexdigest())
        result["total_downloaded_body_bytes"] = total_bytes
        save_receipt(result)
        print(json.dumps({"file": item["external_filename"], "status": row["status"], "bytes": row["observed_bytes"]}), flush=True)
    count = sum(x["status"] == "recovered_exact_historical_bytes" for x in result["records"])
    result.update({"finished_at_utc": now(), "recovered_count": count, "denominator": 12, "status": "complete_exact_recovery" if count == 12 else "recovery_incomplete", "remaining_limitations": protocol["scope"]})
    save_receipt(result)


def verify():
    protocol = json.loads(PROTOCOL.read_text())
    check_bindings(protocol)
    receipt = json.loads(RECEIPT.read_text())
    assert receipt["protocol_sha256"] == digest(PROTOCOL)
    assert len(receipt["records"]) == 12
    successes = 0
    for expected, row in zip(protocol["files"], receipt["records"]):
        assert expected["source_path"] == row["source_path"]
        assert row["logical_attempts"] in (0, 1)
        if row["status"] == "recovered_exact_historical_bytes":
            path = Path(row["external_path"])
            assert path.stat().st_size == row["observed_bytes"] == expected["expected_bytes"]
            assert digest(path) == row["observed_sha256"] == expected["expected_sha256"]
            successes += 1
    assert successes == receipt["recovered_count"]
    print(json.dumps({"verification": "passed", "verified_source_objects": successes, "denominator": 12, "protocol_sha256": digest(PROTOCOL), "receipt_sha256": digest(RECEIPT)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "recover", "verify"))
    globals()[parser.parse_args().action]()

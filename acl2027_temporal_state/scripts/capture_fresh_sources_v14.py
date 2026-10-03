#!/usr/bin/env python3
"""Retrieve only the twelve frozen HTML sources, once each; retain public receipts."""
import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat()

def sanitized_url(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--external-dir", type=Path, required=True)
    ap.add_argument("--receipt", type=Path, required=True)
    args = ap.parse_args()
    raw = args.protocol.read_bytes()
    protocol = json.loads(raw)
    policy = protocol["download_policy"]
    assert policy["logical_attempts_per_file"] == 1
    assert len(protocol["selected"]) == 6
    if args.receipt.exists():
        raise FileExistsError("Existing receipt; do not retry or overwrite frozen attempt")
    files_dir = args.external_dir / "source_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted([f for pair in protocol["selected"] for f in pair["files"]],key=lambda f:f["download_order"])
    assert len(sources) == 12
    assert all(not (files_dir / Path(f["path"]).name).exists() for f in sources)
    receipt = {"schema_version":"fresh_source_capture_receipt_v14", "started_at_utc":now(),
        "protocol_sha256":sha256(raw), "script_sha256":sha256(Path(__file__).read_bytes()),
        "dataset_revision":protocol["revision"], "source_storage_external":True,
        "source_documents_redistributed":False,
        "scope":"Source acquisition only; transport/integrity is not source admission, faithful extraction, historical certification, or benchmark validation.",
        "response_headers_policy":"Only content type/length and ETag retained publicly. Redirect query strings are stripped to avoid publishing transient signed-URL parameters.",
        "records":[]}
    total = 0
    def checkpoint():
        receipt["total_downloaded_body_bytes"] = total
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2)+"\n")
    checkpoint()
    for item in sources:
        record = {"download_order":item["download_order"],"source_path":item["path"],
            "source_url":item["source_url"],"expected_bytes":item["size"],"expected_git_blob_sha1":item["oid"],
            "attempt_started_at_utc":now(),"logical_attempts":0,"status":"not_attempted"}
        receipt["records"].append(record)
        remaining = policy["max_total_downloaded_body_bytes"] - total
        cap = min(policy["max_bytes_per_file"], remaining)
        if item["size"] > cap:
            record["status"] = "skipped_known_oversize_or_total_budget"
            checkpoint()
            continue
        path = files_dir / Path(item["path"]).name
        record["logical_attempts"] = 1
        try:
            request = urllib.request.Request(item["source_url"],headers={"User-Agent":"WikiGraphRag-source-feasibility/0.14", "Accept-Encoding":"identity"})
            with urllib.request.urlopen(request, timeout=policy["timeout_seconds_per_network_operation"]) as response:
                record.update({"http_status":response.status,"final_url_without_query":sanitized_url(response.url),
                    "content_type":response.headers.get("Content-Type"),"content_length":response.headers.get("Content-Length"),
                    "etag":response.headers.get("ETag")})
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > cap:
                    record["status"] = "rejected_declared_oversize_or_total_budget"
                else:
                    count = 0
                    with path.open("xb") as stream:
                        while count < cap:
                            block = response.read(min(1024*1024, cap-count))
                            if not block:
                                break
                            stream.write(block)
                            count += len(block)
                            total += len(block)
                    record["downloaded_bytes"] = count
                    record["external_relative_path"] = str(Path("source_files")/path.name)
                    data = path.read_bytes()
                    record["sha256"] = sha256(data)
                    record["observed_git_blob_sha1"] = hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
                    record["size_matches_listing"] = count == item["size"]
                    record["git_blob_matches_listing"] = record["observed_git_blob_sha1"] == item["oid"]
                    if count == cap and count != item["size"]:
                        record["status"] = "stopped_at_byte_cap_partial"
                    elif record["size_matches_listing"] and record["git_blob_matches_listing"]:
                        record["status"] = "retrieved_exact_released_object"
                    else:
                        record["status"] = "retrieved_integrity_mismatch"
        except urllib.error.HTTPError as e:
            record.update({"status":"http_error", "http_status":e.code, "error_type":type(e).__name__})
        except Exception as e:
            record.update({"status":"retrieval_error", "error_type":type(e).__name__, "error":str(e)[:500]})
            if path.exists():
                data = path.read_bytes()
                record.update({"partial_bytes":len(data), "partial_sha256":sha256(data), "external_relative_path":str(Path("source_files")/path.name)})
        record["attempt_finished_at_utc"] = now()
        checkpoint()
        print(json.dumps({"source_path":item["path"],"status":record["status"],"downloaded_bytes":record.get("downloaded_bytes",record.get("partial_bytes",0))}),flush=True)
    receipt["completed_at_utc"] = now()
    receipt["summary"] = {"selected_sources":12,"exact_released_objects":sum(x["status"]=="retrieved_exact_released_object" for x in receipt["records"]),
        "failed_skipped_or_mismatched":sum(x["status"]!="retrieved_exact_released_object" for x in receipt["records"]),
        "replacements":0,"new_QA_created":0,"native_predictions_inspected":0}
    checkpoint()
    print(json.dumps({"receipt":str(args.receipt),"sha256":sha256(args.receipt.read_bytes()),"total_bytes":total,"summary":receipt["summary"]}),flush=True)

if __name__ == "__main__":
    main()

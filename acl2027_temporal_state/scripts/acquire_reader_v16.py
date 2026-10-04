#!/usr/bin/env python3
"""Acquire one pinned reader weight with bounded memory, space and provenance.

No inference, credentials, model fallback, overwrite, retry or implicit resume.
A failed partial file and its receipt remain available for an explicit decision.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
SCHEMA = "reader_acquisition_protocol_v16"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".writing")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class BoundViolation(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise BoundViolation(code)


def telemetry(destination_directory):
    stat = os.statvfs(destination_directory)
    return {
        "memory_current_bytes": int(Path("/sys/fs/cgroup/memory.current").read_text()),
        "memory_max_bytes": int(Path("/sys/fs/cgroup/memory.max").read_text()),
        "filesystem_available_bytes": stat.f_bavail * stat.f_frsize,
    }


class HTTPSRedirectsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        require(parsed.scheme == "https" and not parsed.username and not parsed.password,
                "non_https_or_credentialed_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate(protocol_path, output_path):
    protocol_path = protocol_path.resolve(strict=True)
    output_path = output_path.resolve()
    p = json.loads(protocol_path.read_text())
    require(p["schema_version"] == SCHEMA, "unexpected_protocol_schema")
    require(protocol_path.is_relative_to(PROJECT), "protocol_must_be_in_project")
    require(output_path.is_relative_to(PROJECT), "receipt_must_be_in_project")
    require(not output_path.exists(), "receipt_exists_no_overwrite")
    require(digest(__file__) == p["acquirer_sha256"], "acquirer_hash_mismatch")
    feasibility = PROJECT / p["feasibility_path"]
    require(digest(feasibility) == p["feasibility_sha256"], "feasibility_hash_mismatch")
    candidate = json.loads(feasibility.read_text())["recommended_candidate"]
    asset = p["asset"]
    require(all(candidate[k] == asset[k] for k in
                ["filename", "repository", "revision", "url", "bytes", "sha256"]),
            "candidate_differs_from_prior_metadata")
    require(asset["url"] == "https://huggingface.co/" + asset["repository"] +
            "/resolve/" + asset["revision"] + "/" + asset["filename"],
            "artifact_url_not_pinned")
    require(type(asset["bytes"]) is int and 0 < asset["bytes"] <= int(3.8 * 2**30),
            "invalid_weight_length")
    destination = Path(p["external_directory"]).resolve(strict=True)
    require(destination.is_relative_to(Path("/dev/shm")) and
            not destination.is_relative_to(REPOSITORY), "invalid_external_destination")
    require(Path(asset["filename"]).name == asset["filename"], "invalid_asset_filename")
    final = destination / asset["filename"]
    partial = destination / (asset["filename"] + ".partial")
    require(not final.exists() and not final.is_symlink(), "final_exists_no_overwrite")
    require(not partial.exists() and not partial.is_symlink(), "partial_exists_no_implicit_resume")
    guard = p["guards"]
    require(guard["minimum_filesystem_reserve_bytes"] >= 512 * 2**20,
            "insufficient_declared_filesystem_reserve")
    require(guard["memory_current_stop_bytes"] == int(7.25 * 2**30),
            "unexpected_total_memory_guard")
    require(guard["block_bytes"] == 1024 * 1024 and
            guard["monitor_interval_seconds"] == 0.1 and
            guard["socket_timeout_seconds"] == 30 and
            guard["maximum_wall_seconds"] == 1800, "unexpected_transfer_guards")
    require(p["weight_download_attempt"] == 1 and not p["resume_allowed"],
            "unexpected_resume_or_attempt")
    base_path = PROJECT / p["runtime"]["base_config"]
    require(digest(base_path) == p["runtime"]["base_config_sha256"],
            "runtime_config_hash_mismatch")
    from fetch_local_backend_v07 import inspect_runtime
    runtime_files = inspect_runtime(Path(p["runtime"]["directory"]),
                                    json.loads(base_path.read_text()))
    return p, destination, final, partial, runtime_files


def acquire(protocol_path, output_path):
    p, directory, final, partial, runtime_files = validate(protocol_path, output_path)
    guard, asset = p["guards"], p["asset"]
    begun = time.monotonic()
    receipt = {
        "schema_version": "reader_acquisition_v16", "status": "started",
        "started_at_utc": now(), "protocol_path": str(protocol_path.relative_to(PROJECT)),
        "protocol_sha256": digest(protocol_path), "acquirer_sha256": digest(__file__),
        "asset": asset, "guards": guard, "external_final_path": str(final),
        "external_partial_path": str(partial), "runtime_files_verified": len(runtime_files),
        "attempt": 1, "resumed": False, "downloaded_bytes": 0,
        "model_loads": 0, "completion_calls": 0, "events": [],
        "resource_samples": 0, "peak_memory_current_bytes": 0,
        "minimum_filesystem_available_bytes": None,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, receipt)
    lock = threading.Lock()
    stopped = threading.Event()
    done = threading.Event()
    failure = []
    downloaded = 0

    def observe():
        sample = telemetry(directory)
        with lock:
            receipt["resource_samples"] += 1
            receipt["peak_memory_current_bytes"] = max(
                receipt["peak_memory_current_bytes"], sample["memory_current_bytes"])
            previous = receipt["minimum_filesystem_available_bytes"]
            receipt["minimum_filesystem_available_bytes"] = sample["filesystem_available_bytes"] \
                if previous is None else min(previous, sample["filesystem_available_bytes"])
        return sample

    def check(sample, remaining):
        require(sample["memory_max_bytes"] == p["expected_cgroup_limit_bytes"],
                "cgroup_limit_changed")
        require(sample["memory_current_bytes"] < guard["memory_current_stop_bytes"],
                "total_memory_guard_exceeded")
        require(sample["filesystem_available_bytes"] >= remaining +
                guard["minimum_filesystem_reserve_bytes"], "filesystem_reserve_would_be_lost")
        require(time.monotonic() - begun <= guard["maximum_wall_seconds"],
                "download_wall_budget_exceeded")

    def watch():
        while not done.wait(guard["monitor_interval_seconds"]):
            try:
                # Disk reserve is checked by the writer using a consistent
                # byte count; this concurrent watcher tracks total cgroup RAM.
                sample = observe()
                require(sample["memory_current_bytes"] < guard["memory_current_stop_bytes"],
                        "total_memory_guard_exceeded")
                require(time.monotonic() - begun <= guard["maximum_wall_seconds"],
                        "download_wall_budget_exceeded")
            except BaseException as error:
                failure.append(str(error) if isinstance(error, BoundViolation)
                               else "telemetry_read_failed")
                stopped.set()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    try:
        initial = observe()
        receipt["preflight_resources"] = initial
        check(initial, asset["bytes"])
        receipt["events"].append({"at_utc": now(), "event": "preflight_passed"})
        write_json(output_path, receipt)
        watcher.start()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), HTTPSRedirectsOnly())
        request = urllib.request.Request(asset["url"], headers={
            "User-Agent": "WikiGraphRag-reader-acquisition-v16", "Accept-Encoding": "identity"})
        checksum = hashlib.sha256()
        with opener.open(request, timeout=guard["socket_timeout_seconds"]) as response:
            require(response.status == 200, "http_status_not_200")
            require(urllib.parse.urlsplit(response.url).scheme == "https", "response_not_https")
            length = response.headers.get("Content-Length")
            require(length is None or int(length) == asset["bytes"], "content_length_mismatch")
            require(response.headers.get("Content-Encoding", "identity").lower() == "identity",
                    "content_encoding_not_identity")
            receipt["http_status"] = response.status
            receipt["http_content_length"] = int(length) if length is not None else None
            with partial.open("xb") as target:
                while True:
                    require(not stopped.is_set(), failure[0] if failure else "watchdog_stopped")
                    check(observe(), asset["bytes"] - downloaded)
                    block = response.read(min(guard["block_bytes"], asset["bytes"] - downloaded + 1))
                    if not block:
                        break
                    require(downloaded + len(block) <= asset["bytes"], "download_exceeded_pinned_length")
                    require(not stopped.is_set(), failure[0] if failure else "watchdog_stopped")
                    target.write(block)
                    checksum.update(block)
                    downloaded += len(block)
                    receipt["downloaded_bytes"] = downloaded
                    if downloaded % (32 * 2**20) == 0:
                        with lock:
                            write_json(output_path, receipt)
                target.flush()
                os.fsync(target.fileno())
        receipt["stream_sha256"] = checksum.hexdigest()
        require(downloaded == asset["bytes"], "download_shorter_than_pinned_length")
        require(checksum.hexdigest() == asset["sha256"], "stream_sha256_mismatch")
        require(not stopped.is_set(), failure[0] if failure else "watchdog_stopped")
        check(observe(), 0)
        # A separate streaming full-file read confirms the bytes on storage;
        # it never creates another weight copy or loads the model.
        receipt["stored_file_sha256"] = digest(partial)
        require(receipt["stored_file_sha256"] == asset["sha256"], "stored_sha256_mismatch")
        require(not stopped.is_set(), failure[0] if failure else "watchdog_stopped")
        check(observe(), 0)
        require(not final.exists() and not final.is_symlink(), "concurrent_final_exists")
        partial.rename(final)
        receipt["final_bytes"] = final.stat().st_size
        receipt["status"] = "completed"
        receipt["events"].append({"at_utc": now(), "event": "exact_weight_verified_and_renamed"})
    except BaseException as error:
        receipt["status"] = "failed"
        receipt["error_type"] = type(error).__name__
        # Never record redirected signed URLs, credentials or raw HTTP errors.
        receipt["failure_code"] = str(error) if isinstance(error, BoundViolation) else "transfer_or_io_error"
        if isinstance(error, urllib.error.HTTPError):
            receipt["http_error_status"] = error.code
        receipt["partial_bytes_retained"] = partial.stat().st_size if partial.exists() else 0
        receipt["events"].append({"at_utc": now(), "event": "failure_retained_no_retry"})
    finally:
        done.set()
        if watcher.is_alive():
            watcher.join(timeout=2)
        receipt["finished_at_utc"] = now()
        receipt["elapsed_seconds"] = round(time.monotonic() - begun, 3)
        receipt["final_resources"] = telemetry(directory)
        write_json(output_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = acquire(args.protocol.resolve(), args.output.resolve())
    print(json.dumps({k: receipt.get(k) for k in [
        "status", "downloaded_bytes", "stored_file_sha256", "elapsed_seconds",
        "peak_memory_current_bytes", "failure_code"]}), flush=True)
    raise SystemExit(0 if receipt["status"] == "completed" else 1)


if __name__ == "__main__":
    main()

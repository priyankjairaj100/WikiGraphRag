#!/usr/bin/env python3
"""Run a frozen JSONL batch with one pinned, bounded native interpreter.

Inputs contain only id, messages, and optional output_schema. No grading occurs.
Preparation makes zero model calls. Execution preserves every raw response.
Resume skips completed calls. An interrupted call is recorded without a retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/interpreter_runtime_v26.json"
MEMORY = Path("/sys/fs/cgroup/memory.current")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def release_model_cache(path):
    with Path(path).open("rb") as stream:
        os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def write(path, value):
    """Write once. Existing research records are never overwritten."""
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_requests(path):
    rows = []
    seen = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank_input_line_{number}")
        row = json.loads(line)
        if not isinstance(row, dict) or not {"id", "messages"} <= row.keys():
            raise ValueError(f"input_schema_{number}")
        if set(row) - {"id", "messages", "output_schema"}:
            raise ValueError(f"unexpected_input_keys_{number}")
        if not isinstance(row["id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", row["id"]):
            raise ValueError(f"unsafe_id_{number}")
        if row["id"] in seen:
            raise ValueError("duplicate_id")
        if not isinstance(row["messages"], list) or not row["messages"]:
            raise ValueError("empty_messages")
        for message in row["messages"]:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise ValueError("message_schema")
            if message["role"] not in {"system", "user", "assistant"}:
                raise ValueError("message_role")
            if not isinstance(message["content"], str):
                raise ValueError("message_content")
        if "output_schema" in row and not isinstance(row["output_schema"], dict):
            raise ValueError("output_schema_type")
        seen.add(row["id"])
        rows.append(row)
    if not rows:
        raise ValueError("empty_batch")
    return rows


def prepare(requests, assets, output):
    requests, assets, output = map(lambda x: Path(x).resolve(), (requests, assets, output))
    config = json.loads(CONFIG.read_text())
    rows = load_requests(requests)
    model = assets / config["model"]["filename"]
    runtime = assets / "runtime" / "llama-b11146" / "llama-server"
    archive = assets / config["runtime"]["filename"]
    for path, spec in [(model, config["model"]), (archive, config["runtime"])]:
        if path.stat().st_size != spec["bytes"] or digest(path) != spec["sha256"]:
            raise ValueError("asset_mismatch")
        release_model_cache(path)
    runtime_files = {str(p.relative_to(runtime.parent)): digest(p)
                     for p in sorted(runtime.parent.iterdir()) if p.is_file()}
    output.mkdir(parents=True, exist_ok=False)
    frozen = {"schema_version": "interpreter_freeze_v26", "requests_path": str(requests),
              "requests_sha256": digest(requests), "request_ids": [r["id"] for r in rows],
              "config": config, "config_sha256": digest(CONFIG),
              "script_sha256": digest(__file__), "model_path": str(model),
              "runtime_path": str(runtime), "runtime_files": runtime_files,
              "prepared_at_unix": time.time(), "model_completions_at_freeze": 0}
    write(output / "freeze.json", frozen)
    return {"status": "frozen", "requests": len(rows), "freeze_sha256": digest(output / "freeze.json")}


def api(port, endpoint, payload=None, timeout=30):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None if payload is None else canonical(payload)
    request = urllib.request.Request(f"http://127.0.0.1:{port}/{endpoint}", data=data,
                                     headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        body = response.read(16 * 2**20 + 1)
    if len(body) > 16 * 2**20:
        raise ValueError("response_byte_limit")
    return body


def record_completion(folder, row, response, input_tokens):
    """Validate transport fields without scoring semantic correctness."""
    limit = response.get("stop_type") == "limit" or response.get("stopped_limit") is True
    truncated = response.get("truncated") is True
    content = response.get("content")
    status = "completed"
    if not isinstance(content, str):
        status = "invalid_response"
    elif truncated:
        status = "input_truncated"
    elif limit:
        status = "output_limit"
    parsed = None
    json_error = None
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            json_error = str(error)
    result = {"id": row["id"], "status": status, "input_tokens": input_tokens,
              "output_tokens": response.get("tokens_predicted"),
              "stop_type": response.get("stop_type"), "output_limit": limit,
              "input_truncated": truncated, "content": content, "parsed_json": parsed,
              "json_parse_error": json_error, "timings": response.get("timings"),
              "raw_response_sha256": digest(folder / "response.raw.json"),
              "finished_at_unix": time.time()}
    write(folder / "result.json", result)
    return result


def recover_record(folder, row):
    """Recover persisted responses without repeating a possibly completed call."""
    result_path = folder / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if result["id"] != row["id"]:
            raise ValueError("resume_id_changed")
        if "raw_response_sha256" in result:
            if digest(folder / "response.raw.json") != result["raw_response_sha256"]:
                raise ValueError("resume_response_changed")
        return result
    response_path = folder / "response.raw.json"
    if response_path.exists():
        prompt = json.loads((folder / "native_prompt.json").read_text())
        return record_completion(folder, row, json.loads(response_path.read_text()), len(prompt["tokens"]))
    if (folder / "call_started.json").exists():
        result = {"id": row["id"], "status": "interrupted_no_retry", "finished_at_unix": time.time()}
        write(result_path, result)
        return result
    if folder.exists():
        # No completion was called. Keep this technical failure visible.
        result = {"id": row["id"], "status": "interrupted_before_call", "finished_at_unix": time.time()}
        write(result_path, result)
        return result
    return None


def run(output, freeze_sha256, max_new_calls, *, preflight_only=False):
    output = Path(output).resolve()
    if digest(output / "freeze.json") != freeze_sha256:
        raise ValueError("freeze_digest_changed")
    frozen = json.loads((output / "freeze.json").read_text())
    if digest(__file__) != frozen["script_sha256"] or digest(CONFIG) != frozen["config_sha256"]:
        raise ValueError("recipe_changed")
    if digest(frozen["requests_path"]) != frozen["requests_sha256"]:
        raise ValueError("requests_changed")
    config = frozen["config"]
    if digest(frozen["model_path"]) != config["model"]["sha256"]:
        raise ValueError("model_changed")
    release_model_cache(frozen["model_path"])
    binary = Path(frozen["runtime_path"])
    for name, expected in frozen["runtime_files"].items():
        if digest(binary.parent / name) != expected:
            raise ValueError("runtime_changed")
    rows = load_requests(frozen["requests_path"])
    if [r["id"] for r in rows] != frozen["request_ids"]:
        raise ValueError("roster_changed")
    import fcntl
    lock = (output / "run.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    pending = []
    for row in rows:
        if preflight_only or recover_record(output / row["id"], row) is None:
            pending.append(row)
    pending = pending[:max_new_calls]
    if not pending:
        lock.close()
        return {"status": "no_pending_requests", "new_calls": 0}
    native = config["native"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    attempt = output / ("execution_%d" % time.time_ns())
    attempt.mkdir()
    if preflight_only:
        (attempt / "preflight").mkdir()
    command = [str(binary), "-m", frozen["model_path"], "--host", "127.0.0.1", "--port", str(port),
               "--threads", str(native["threads"]), "--threads-batch", str(native["threads"]),
               "--ctx-size", str(native["context_tokens"]), "--parallel", "1",
               "--batch-size", str(native["batch_tokens"]), "--ubatch-size", str(native["ubatch_tokens"]),
               "--cache-type-k", native["cache_type_k"], "--cache-type-v", native["cache_type_v"],
               "--flash-attn", "on", "--load-mode", "mmap", "--reasoning", "off",
               "--no-webui", "--no-context-shift"]
    write(attempt / "command.json", command)
    started = time.monotonic()
    stop = threading.Event()
    failures = []
    samples = []
    process = None
    calls = 0
    watcher = None
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (native["address_space_limit_bytes"],) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def monitor():
        while not stop.wait(0.25):
            try:
                memory = int(MEMORY.read_text())
                samples.append({"elapsed": time.monotonic() - started, "memory_current": memory})
                if memory >= native["memory_current_stop_bytes"]:
                    raise RuntimeError("memory_limit")
                if time.monotonic() - started >= native["wall_seconds"]:
                    raise RuntimeError("wall_limit")
            except Exception as error:
                failures.append(str(error))
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                return
    def check():
        if failures:
            raise RuntimeError(failures[0])
        if process.poll() is not None:
            raise RuntimeError("native_process_exited")
    try:
        if int(MEMORY.read_text()) >= native["memory_current_stop_bytes"] - 2**30:
            raise RuntimeError("prelaunch_memory_reserve")
        with (attempt / "server.log").open("xb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                        env=dict(os.environ, LD_LIBRARY_PATH=str(binary.parent)),
                        start_new_session=True, preexec_fn=limits)
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
            while True:
                check()
                if time.monotonic() - started > native["startup_seconds"]:
                    raise RuntimeError("startup_timeout")
                try:
                    if json.loads(api(port, "health", timeout=1)).get("status") == "ok":
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            props = json.loads(api(port, "props"))
            write(attempt / "props.json", props)
            if props.get("build_info") != "b11146-7fe450e19":
                raise RuntimeError("runtime_build_mismatch")
            if props.get("model_path") != frozen["model_path"] or props.get("total_slots") != 1:
                raise RuntimeError("native_identity_mismatch")
            if props.get("default_generation_settings", {}).get("n_ctx") != native["context_tokens"]:
                raise RuntimeError("native_context_mismatch")
            for row in pending:
                check()
                folder = (attempt / "preflight" / row["id"]) if preflight_only else (output / row["id"])
                folder.mkdir()
                write(folder / "input.json", row)
                try:
                    template = json.loads(api(port, "apply-template", {"messages": row["messages"]}))
                    tokenized = json.loads(api(port, "tokenize", {"content": template["prompt"],
                                             "add_special": True, "parse_special": True}))
                    tokens = tokenized["tokens"]
                    write(folder / "native_prompt.json", {"prompt": template["prompt"], "tokens": tokens})
                    if len(tokens) + config["generation"]["n_predict"] > native["context_tokens"]:
                        raise ValueError("prompt_exceeds_context")
                    if preflight_only:
                        write(folder / "result.json", {"id": row["id"], "status": "preflight_passed",
                              "input_tokens": len(tokens), "reserved_output_tokens": config["generation"]["n_predict"],
                              "model_completions": 0})
                        print(json.dumps({"id": row["id"], "status": "preflight_passed", "input_tokens": len(tokens)}), flush=True)
                        continue
                    request = dict(config["generation"], prompt=tokens, return_tokens=True)
                    if "output_schema" in row:
                        request["json_schema"] = row["output_schema"]
                    write(folder / "completion_request.json", request)
                    check()
                    write(folder / "call_started.json", {"started_at_unix": time.time(),
                          "request_sha256": hashlib.sha256(canonical(request)).hexdigest()})
                    calls += 1
                    raw = api(port, "completion", request, timeout=native["request_seconds"])
                    with (folder / "response.raw.json").open("xb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    response = json.loads(raw)
                    result = record_completion(folder, row, response, len(tokens))
                    print(json.dumps({"id": row["id"], "status": result["status"]}), flush=True)
                    check()
                except Exception as error:
                    if not (folder / "result.json").exists():
                        write(folder / "result.json", {"id": row["id"], "status": "technical_failure",
                              "error_type": type(error).__name__, "error": str(error),
                              "completion_started": (folder / "call_started.json").exists()})
                    if isinstance(error, ValueError) and str(error) == "prompt_exceeds_context":
                        continue
                    raise
    finally:
        stop.set()
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if watcher is not None:
            watcher.join(timeout=2)
        write(attempt / "receipt.json", {"preflight_only": preflight_only, "new_calls": calls, "elapsed_seconds": time.monotonic() - started,
              "failures": failures, "memory_samples": samples,
              "process_returncode": None if process is None else process.poll()})
        lock.close()
    return {"status": "batch_finished", "new_calls": calls, "execution_path": str(attempt)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--requests", required=True)
    prep.add_argument("--assets", required=True)
    prep.add_argument("--output", required=True)
    execute = sub.add_parser("run")
    execute.add_argument("--output", required=True)
    execute.add_argument("--freeze-sha256", required=True)
    execute.add_argument("--max-new-calls", type=int, default=1)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output", required=True)
    preflight.add_argument("--freeze-sha256", required=True)
    preflight.add_argument("--max-requests", type=int, default=1000)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.requests, args.assets, args.output)
    elif args.command == "run":
        if args.max_new_calls <= 0:
            raise ValueError("positive_call_cap_required")
        result = run(args.output, args.freeze_sha256, args.max_new_calls)
    else:
        if args.max_requests <= 0:
            raise ValueError("positive_request_cap_required")
        result = run(args.output, args.freeze_sha256, args.max_requests, preflight_only=True)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()

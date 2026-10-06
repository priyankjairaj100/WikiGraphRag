#!/usr/bin/env python3
"""Count texts or rendered messages with the pinned native tokenizer.

Input JSONL rows contain {id,text} or {id,messages}. No generation endpoint exists
in this helper. Outputs retain native token IDs and exact rendered prompts.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import threading
import time

import run_interpretation_v26 as runtime


def execute(inputs, assets, output):
    inputs, assets, output = map(lambda value: Path(value).resolve(), (inputs, assets, output))
    config = json.loads(runtime.CONFIG.read_text())
    native = config["native"]
    model = assets / config["model"]["filename"]
    binary = assets / "runtime" / "llama-b11146" / "llama-server"
    if runtime.digest(model) != config["model"]["sha256"]:
        raise ValueError("model_mismatch")
    runtime.release_model_cache(model)
    rows = []
    seen = set()
    for line in inputs.read_text().splitlines():
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) not in ({"id", "text"}, {"id", "messages"}):
            raise ValueError("tokenizer_input_schema")
        if not isinstance(row["id"], str) or row["id"] in seen:
            raise ValueError("tokenizer_duplicate_or_invalid_id")
        if "text" in row and not isinstance(row["text"], str):
            raise ValueError("text_type")
        if "messages" in row and not isinstance(row["messages"], list):
            raise ValueError("messages_type")
        seen.add(row["id"])
        rows.append(row)
    if not 1 <= len(rows) <= 20000:
        raise ValueError("tokenizer_batch_size")
    output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
               "--threads", str(native["threads"]), "--threads-batch", str(native["threads"]),
               "--ctx-size", str(native["context_tokens"]), "--parallel", "1",
               "--batch-size", str(native["batch_tokens"]), "--ubatch-size", str(native["ubatch_tokens"]),
               "--cache-type-k", native["cache_type_k"], "--cache-type-v", native["cache_type_v"],
               "--flash-attn", "on", "--load-mode", "mmap", "--reasoning", "off",
               "--no-webui", "--no-context-shift"]
    runtime.write(output / "freeze.json", {"input_sha256": runtime.digest(inputs),
                  "config_sha256": runtime.digest(runtime.CONFIG), "config": config,
                  "script_sha256": runtime.digest(__file__), "helper_sha256": runtime.digest(runtime.__file__),
                  "runtime_files": {p.name: runtime.digest(p) for p in sorted(binary.parent.iterdir()) if p.is_file()},
                  "command": command, "generation_calls_allowed": 0})
    process = None
    watcher = None
    failures = []
    stopped = threading.Event()
    started = time.monotonic()
    peak = 0
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (native["address_space_limit_bytes"],) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def check():
        if failures:
            raise RuntimeError(failures[0])
        if process.poll() is not None:
            raise RuntimeError("native_tokenizer_exited")
    def monitor():
        nonlocal peak
        while not stopped.wait(0.25):
            try:
                memory = int(runtime.MEMORY.read_text())
                peak = max(peak, memory)
                if memory >= native["memory_current_stop_bytes"]:
                    raise RuntimeError("tokenizer_memory_limit")
                if time.monotonic() - started > 1200:
                    raise RuntimeError("tokenizer_wall_limit")
            except Exception as error:
                failures.append(str(error))
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                return
    count = 0
    try:
        if int(runtime.MEMORY.read_text()) >= native["memory_current_stop_bytes"] - 2**30:
            raise RuntimeError("prelaunch_memory_reserve")
        with (output / "server.log").open("xb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                        env=dict(os.environ, LD_LIBRARY_PATH=str(binary.parent)),
                        start_new_session=True, preexec_fn=limits)
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
            while True:
                check()
                if time.monotonic() - started > native["startup_seconds"]:
                    raise RuntimeError("tokenizer_startup_limit")
                try:
                    if json.loads(runtime.api(port, "health", timeout=1)).get("status") == "ok":
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            props = json.loads(runtime.api(port, "props"))
            runtime.write(output / "props.json", props)
            if props.get("build_info") != "b11146-7fe450e19" or props.get("model_path") != str(model):
                raise RuntimeError("tokenizer_identity_mismatch")
            with (output / "counts.jsonl").open("x") as stream:
                for row in rows:
                    check()
                    rendered = None
                    if "messages" in row:
                        rendered = json.loads(runtime.api(port, "apply-template", {"messages": row["messages"]}))["prompt"]
                    text = rendered if rendered is not None else row["text"]
                    special = rendered is not None
                    tokens = json.loads(runtime.api(port, "tokenize", {"content": text,
                               "add_special": special, "parse_special": special}))["tokens"]
                    result = {"id": row["id"], "token_count": len(tokens), "tokens": tokens,
                              "mode": "rendered_messages" if special else "raw_text",
                              "add_special": special, "parse_special": special}
                    if special:
                        result["rendered_prompt"] = rendered
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                    stream.flush()
                    count += 1
            check()
    finally:
        stopped.set()
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if watcher is not None:
            watcher.join(timeout=2)
        runtime.write(output / "receipt.json", {"rows_counted": count, "requested_rows": len(rows),
                      "generation_calls": 0, "elapsed_seconds": time.monotonic() - started,
                      "peak_cgroup_memory_bytes": peak, "failures": failures,
                      "process_returncode": None if process is None else process.poll()})
    return {"rows_counted": count, "generation_calls": 0, "counts_path": str(output / "counts.jsonl")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--assets", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.input, args.assets, args.output)))


if __name__ == "__main__":
    main()

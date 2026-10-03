#!/usr/bin/env python3
"""Bounded, pinned local llama.cpp backend for the v0.7 development pilot.

Weights and runtime live outside the distributable project. No automatic
downloads or fallback models. HTTP is loopback only; llama-server is always
terminated when the context exits. See configs/local_backend_v07.json.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS = PROJECT.parent / "tmp/local_backend_v07"
CONFIG = PROJECT / "configs/local_backend_v07.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def post(port: int, endpoint: str, payload: dict, timeout: float = 180) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/{endpoint.lstrip('/')}",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def verify_assets(asset_root: Path = DEFAULT_ASSETS) -> dict:
    config = json.loads(CONFIG.read_text())
    records = []
    for spec in config["assets"]:
        path = asset_root / spec["relative_path"]
        actual = digest(path)
        if actual != spec["sha256"] or path.stat().st_size != spec["bytes"]:
            raise ValueError(f"asset size or hash mismatch: {spec['relative_path']}")
        records.append({**spec, "verified_sha256": actual})
    runtime = asset_root / config["runtime_directory"]
    executable = runtime / "llama-server"
    files = [{"path": str(p.relative_to(runtime)), "bytes": p.stat().st_size,
              "sha256": digest(p)} for p in sorted(runtime.iterdir()) if p.is_file()]
    # Bind every executable/shared-library byte, not just the launcher.
    expected = config.get("runtime_files")
    if expected is not None and files != expected:
        raise ValueError("extracted runtime file manifest mismatch")
    return {"assets": records, "runtime_files": files,
            "runtime_files_sha256": canonical_hash(files),
            "runtime_executable_sha256": digest(executable)}


@contextmanager
def server(*, asset_root: Path = DEFAULT_ASSETS, context: int = 8192,
           port: int = 18087, log_path: Path, cpu_seconds: int = 3600):
    config = json.loads(CONFIG.read_text())
    if context < 128 or context > config["maximum_context_tokens"]:
        raise ValueError("context outside frozen bounds")
    binding = verify_assets(asset_root)
    runtime = asset_root / config["runtime_directory"]
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(runtime)
    threads = config.get("threads", 2)
    env["OMP_NUM_THREADS"] = str(threads)
    command = [str(runtime / "llama-server"), "-m", str(asset_root / config["model_file"]),
               "--host", "127.0.0.1", "--port", str(port), "-c", str(context),
               "-t", str(threads), "-tb", str(threads), "-b", str(config.get("batch_tokens", 128)),
               "-ub", str(config.get("microbatch_tokens", 128)),
               "-ngl", "0", "--parallel", "1", "--no-warmup", "--no-webui",
               "-ctk", config.get("kv_cache_key_type", "f16"),
               "-ctv", config.get("kv_cache_value_type", "f16"),
               "-fa", config.get("flash_attention", "auto"),
               "--no-context-shift", "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}']

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (config["address_space_limit_bytes"],) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, preexec_fn=limits)
        try:
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"llama-server exited with {process.returncode}; see {log_path}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                        health = json.load(r)
                    if health.get("status") == "ok":
                        break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() - start > 60:
                    raise TimeoutError("backend startup exceeded 60 seconds")
                time.sleep(0.1)
            binding.update({"command": command, "context_tokens": context,
                            "address_space_limit_bytes": config["address_space_limit_bytes"],
                            "cpu_seconds_limit": cpu_seconds, "startup_seconds": time.monotonic() - start,
                            "port": port, "process_id": process.pid})
            yield binding
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            binding["elapsed_seconds"] = time.monotonic() - start
            binding["process_exit_code"] = process.returncode
            binding["children_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss


def render_and_tokenize(port: int, messages: list[dict]) -> dict:
    rendered = post(port, "apply-template", {"messages": messages})
    prompt = rendered["prompt"]
    tokens = post(port, "tokenize", {"content": prompt, "add_special": True,
                                    "parse_special": True})["tokens"]
    return {"rendered_prompt": prompt, "input_token_ids": tokens,
            "rendered_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "input_token_ids_sha256": canonical_hash(tokens), "input_tokens": len(tokens)}


def one_token_request(token_ids: list[int], n_probs: int = 256) -> dict:
    config = json.loads(CONFIG.read_text())
    return {"prompt": token_ids, "n_predict": 1, "temperature": -1.0,
            "seed": 7, "n_probs": n_probs, "post_sampling_probs": False,
            "cache_prompt": config.get("cache_prompt", False), "return_tokens": True, "stream": False,
            "repeat_penalty": 1.0, "presence_penalty": 0.0, "frequency_penalty": 0.0,
            "logit_bias": [], "samplers": ["temperature"]}


def smoke(output: Path, asset_root: Path, context: int) -> dict:
    report = {"schema_version": "local_backend_smoke_v0.7", "status": "started"}
    try:
        with server(asset_root=asset_root, context=context,
                    log_path=output.with_suffix(".server.log"), cpu_seconds=120) as binding:
            report["binding"] = binding
            props = json.load(urllib.request.urlopen(f"http://127.0.0.1:{binding['port']}/props"))
            report["props"] = props
            prompt = render_and_tokenize(binding["port"], [
                {"role": "system", "content": "Respond with exactly Yes or No. /no_think"},
                {"role": "user", "content": "Does the source say Earth has one moon? Source: Earth has one moon. Answer:"}])
            if prompt["input_tokens"] + 1 > context:
                raise ValueError("prompt exceeds context")
            report["prompt"] = prompt
            report["label_tokens"] = {label: post(binding["port"], "tokenize", {
                "content": label, "add_special": False, "parse_special": False})["tokens"]
                for label in ("Yes", "No")}
            request = one_token_request(prompt["input_token_ids"])
            report["request"] = request
            report["response"] = post(binding["port"], "completion", request, 60)
            report["status"] = "completed"
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        write_json(output, report)
        raise
    write_json(output, report)
    return report


def collect(request_path: Path, output_dir: Path, asset_root: Path, context: int,
            receipt_path: Path | None = None):
    """Collect both label probabilities from the same unconstrained position.

    Preflight all token counts and exact label boundaries before any scored
    completion. Raw top-256 responses and evaluated token arrays are retained.
    A missing label, truncation, or count mismatch aborts without retries.
    """
    import prepare_likelihood_scoring_v07 as protocol
    request = json.loads(request_path.read_text())
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "measurements.json").exists():
        raise ValueError("refusing to overwrite an existing completed score run")
    config = json.loads(CONFIG.read_text())
    run = {"schema_version": "local_backend_scoring_receipt_v0.7", "status": "started",
           "request_sha256": canonical_hash(request), "request_file_sha256": digest(request_path),
           "backend_config_sha256": digest(CONFIG), "model_id": config["model_id"],
           "model_sha256": config["model_sha256"], "model_repo_revision": config["model_repo_revision"],
           "runtime_commit": config["runtime_commit"], "runtime_release": config["runtime_release"],
           "rerun_absolute_tolerance": 1e-6, "rerun_policy": "first primary prompt, repeated after all64; no retry or averaging"}
    if receipt_path is None:
        receipt_path = PROJECT / "results/local_backend_v07_scoring_receipt.json"
        log_path = PROJECT / "results/local_backend_v07_scoring.server.log"
    else:
        log_path = receipt_path.with_suffix(".server.log")
    write_json(receipt_path, run)
    try:
        with server(asset_root=asset_root, context=context,
                    log_path=log_path,
                    cpu_seconds=7200) as execution:
            port = execution["port"]
            props = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/props"))
            write_json(output_dir / "server_props.json", props)
            template = props["chat_template"]
            labels = {label: post(port, "tokenize", {"content": label, "add_special": False,
                                                    "parse_special": False})["tokens"]
                      for label in ("Yes", "No")}
            if any(len(t) != 1 for t in labels.values()):
                raise ValueError("label not a single token")
            prompts = []
            for item in request["items"]:
                prompt = render_and_tokenize(port, item["messages"])
                if prompt["input_tokens"] + 1 > context:
                    raise ValueError(f"context overflow for {item['condition_id']}/{item['item_id']}: {prompt['input_tokens']}")
                if not prompt["rendered_prompt"].endswith("<think>\n\n</think>\n\n"):
                    raise ValueError("expected native nonthinking generation prefix absent")
                prompt["label_appended_token_ids"] = {}
                for label, ids in labels.items():
                    appended = post(port, "tokenize", {"content": prompt["rendered_prompt"] + label,
                        "add_special": True, "parse_special": True})["tokens"]
                    if appended != prompt["input_token_ids"] + ids:
                        raise ValueError("label append changes prefix tokenization")
                    prompt["label_appended_token_ids"][label] = appended
                prompts.append({"item_id": item["item_id"], "condition_id": item["condition_id"],
                                "messages_sha256": item["messages_sha256"], **prompt})
            prompt_path = output_dir / "rendered_prompts.jsonl"
            prompt_path.write_text("".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in prompts))
            binding = {"schema_version": "local_backend_binding_v0.7",
                "model_id": config["model_id"], "model_sha256": config["model_sha256"],
                "model_repo_revision": config["model_repo_revision"], "runtime_commit": config["runtime_commit"],
                "runtime_release": config["runtime_release"], "runtime_files_sha256": execution["runtime_files_sha256"],
                "runtime_executable_sha256": execution["runtime_executable_sha256"],
                "backend_config_sha256": digest(CONFIG), "request_sha256": canonical_hash(request),
                "chat_template_sha256": hashlib.sha256(template.encode()).hexdigest(),
                "server_props_sha256": digest(output_dir / "server_props.json"),
                "rendered_prompts_sha256": digest(prompt_path), "context_tokens": context,
                "input_tokens_min": min(p["input_tokens"] for p in prompts),
                "input_tokens_max": max(p["input_tokens"] for p in prompts),
                "label_token_ids": {label: ids[0] for label, ids in labels.items()},
                "threads": config.get("threads", 2), "batch_tokens": config.get("batch_tokens", 128),
                "microbatch_tokens": config.get("microbatch_tokens", 128),
                "address_space_limit_bytes": config["address_space_limit_bytes"],
                "completion_settings": one_token_request([]),
                "full_vocab_logprobs": True, "exact_label_prefix_verified": True,
                "context_untruncated": True, "no_logit_bias": True, "labels_unconstrained": True,
                "softmax_evidence_sha256": digest(PROJECT / "results/local_backend_v07_softmax_evidence.txt")}
            write_json(output_dir / "backend_binding.json", binding)
            run.update({"binding_sha256": canonical_hash(binding), "binding_file_sha256": digest(output_dir / "backend_binding.json"),
                        "runtime_files_sha256": binding["runtime_files_sha256"],
                        "chat_template_sha256": binding["chat_template_sha256"],
                        "preflight_prompt_count": len(prompts), "input_tokens_min": binding["input_tokens_min"],
                        "input_tokens_max": binding["input_tokens_max"]})
            write_json(receipt_path, run)
            print(json.dumps({"preflight": "passed", "prompts": len(prompts), "token_min": binding["input_tokens_min"],
                              "token_max": binding["input_tokens_max"]}), flush=True)
            rows, raw_path = [], output_dir / "raw_backend_responses.jsonl"
            first_response = None
            with raw_path.open("w") as raw_out:
                for index, prompt in enumerate(prompts):
                    started = time.monotonic()
                    payload = one_token_request(prompt["input_token_ids"])
                    response = post(port, "completion", payload, timeout=config.get("per_request_timeout_seconds", 180))
                    raw_out.write(json.dumps({"item_id": prompt["item_id"], "condition_id": prompt["condition_id"],
                        "request": payload, "response": response}, ensure_ascii=False, sort_keys=True) + "\n")
                    raw_out.flush()
                    if response.get("truncated") or response.get("tokens_evaluated") != prompt["input_tokens"]:
                        raise ValueError("response truncated or input token count mismatch")
                    if response.get("tokens_predicted") != 1:
                        raise ValueError("expected one next-token distribution")
                    probabilities = response["completion_probabilities"]
                    if len(probabilities) != 1:
                        raise ValueError("expected one probability position")
                    probs = {p["id"]: p["logprob"] for p in probabilities[0]["top_logprobs"]}
                    if not all(ids[0] in probs for ids in labels.values()):
                        raise ValueError("one or both label tokens absent from top256; no score inferred")
                    row = {key: prompt[key] for key in ("item_id", "condition_id", "messages_sha256", "rendered_prompt_sha256",
                                                       "input_token_ids_sha256", "input_tokens")}
                    row.update({"yes_token_id": labels["Yes"][0], "no_token_id": labels["No"][0],
                                "yes_logprob": probs[labels["Yes"][0]], "no_logprob": probs[labels["No"][0]]})
                    rows.append(row)
                    if first_response is None:
                        first_response = row
                    run["completed_rows"] = len(rows)
                    write_json(receipt_path, run)
                    print(json.dumps({"row": index + 1, "of": len(prompts), "seconds": round(time.monotonic() - started, 3)}), flush=True)
            measurement = {"schema_version": "likelihood_measurements_v0.7", "request_sha256": canonical_hash(request),
                           "backend_binding_sha256": canonical_hash(binding), "rows": rows}
            validation = protocol.validate_measurements(measurement, request, backend_binding_sha256=canonical_hash(binding))
            # One predeclared same-run numerical replay, no rescoring or replacement.
            replay_payload = one_token_request(prompts[0]["input_token_ids"])
            replay = post(port, "completion", replay_payload, timeout=config.get("per_request_timeout_seconds", 180))
            if replay.get("truncated") or replay.get("tokens_evaluated") != prompts[0]["input_tokens"] or replay.get("tokens_predicted") != 1:
                raise ValueError("numerical replay truncated or token counts differ")
            write_json(output_dir / "numerical_replay_raw.json", {"request": replay_payload, "response": replay})
            replay_probs = {p["id"]: p["logprob"] for p in replay["completion_probabilities"][0]["top_logprobs"]}
            errors = {label: abs(replay_probs[ids[0]] - first_response[label.lower() + "_logprob"])
                      for label, ids in labels.items()}
            run["numerical_replay"] = {"absolute_errors": errors, "tolerance": 1e-6,
                "passed": all(x <= 1e-6 for x in errors.values()),
                "tokens_evaluated": replay.get("tokens_evaluated"), "truncated": replay.get("truncated")}
            write_json(output_dir / "measurements.json", measurement)
            write_json(output_dir / "measurement_validation.json", validation)
            run.update({"status": "completed", "measurement_sha256": digest(output_dir / "measurements.json"),
                "raw_measurements_sha256": digest(raw_path), "rendered_prompts_sha256": digest(prompt_path)})
        run["execution"] = execution
        write_json(receipt_path, run)
    except BaseException as error:
        run.update({"status": "failed", "error_type": type(error).__name__, "error": str(error)})
        write_json(receipt_path, run)
        raise
    return run


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--context", type=int, default=2048)
    parser.add_argument("--smoke-output", type=Path, default=PROJECT / "results/local_backend_v07_smoke.json")
    parser.add_argument("--score-request", type=Path)
    parser.add_argument("--score-output-dir", type=Path, default=PROJECT / "data/likelihood_scoring_v07")
    parser.add_argument("--score-receipt", type=Path,
                        help="Fresh receipt path; server log is written beside it, preserving canonical evidence")
    args = parser.parse_args()
    if args.score_request:
        result = collect(args.score_request, args.score_output_dir, args.assets, args.context,
                         receipt_path=args.score_receipt)
        print(json.dumps({"status": result["status"], "completed_rows": result.get("completed_rows")}))
    else:
        result = smoke(args.smoke_output, args.assets, args.context)
        print(json.dumps({"status": result["status"], "input_tokens": result.get("prompt", {}).get("input_tokens"),
                          "peak_rss_kib": result.get("binding", {}).get("children_peak_rss_kib"),
                          "generated": result.get("response", {}).get("content")}))

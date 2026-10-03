# /// script
# dependencies = ["transformers==4.51.3", "torch==2.6.0", "accelerate==1.6.0"]
# requires-python = ">=3.12,<3.13"
# ///
"""Standalone model pilot template. Builder embeds only source-prefix inputs.

Default: verify payload without imports/network/model execution. Inference needs
both an enabled embedded config and --run-model. No cloud submission code exists.
"""
import argparse
import base64
import gzip
import hashlib
import importlib.metadata
import json
import math
import platform
from datetime import date
from pathlib import Path
import sys
import time

PAYLOAD_B64 = "__MODEL_PILOT_PAYLOAD__"
MAX_RESULT_BYTES = 2_000_000
MAX_ENCODED_BYTES = 1_000_000
CHUNK_CHARS = 6000


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError("nonfinite_json_constant")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def valid_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("noncanonical_day")
    return value


def validate_schema(value, spec, root=None, at="$"):
    """Fail-closed validator for the deliberately small schema vocabulary."""
    root = spec if root is None else root
    allowed = {"$schema", "$defs", "$ref", "type", "properties", "required",
               "additionalProperties", "items", "minItems", "maxItems",
               "minLength", "format", "enum", "const", "anyOf"}
    if set(spec) - allowed:
        raise ValueError("unsupported_schema_keyword:" + at)
    if "$ref" in spec:
        ref = spec["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError("external_schema_ref")
        return validate_schema(value, root["$defs"][ref.split("/")[-1]], root, at)
    if "anyOf" in spec:
        for option in spec["anyOf"]:
            try:
                validate_schema(value, option, root, at)
                return
            except (ValueError, TypeError):
                pass
        raise ValueError("anyOf_failed:" + at)
    if "const" in spec and value != spec["const"]:
        raise ValueError("const_failed:" + at)
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("enum_failed:" + at)
    kind = spec.get("type")
    types = {"object": dict, "array": list, "string": str, "null": type(None)}
    if kind is not None and (kind not in types or type(value) is not types[kind]):
        raise ValueError("type_failed:" + at)
    if kind == "object":
        if set(spec.get("required", [])) - set(value):
            raise ValueError("missing_fields:" + at)
        props = spec.get("properties", {})
        if spec.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError("unknown_fields:" + at)
        for key, item in value.items():
            validate_schema(item, props[key], root, at + "." + key)
    if kind == "array":
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", math.inf):
            raise ValueError("array_length_failed:" + at)
        for i, item in enumerate(value):
            validate_schema(item, spec["items"], root, at + "[" + str(i) + "]")
    if kind == "string":
        if len(value) < spec.get("minLength", 0):
            raise ValueError("string_length_failed:" + at)
        if spec.get("format") == "date":
            valid_day(value)


def verify_payload(payload):
    if digest(payload["prompt"].encode()) != payload["prompt_sha256"]:
        raise ValueError("prompt_hash_mismatch")
    if digest(canonical(payload["output_schema"])) != payload["schema_canonical_sha256"]:
        raise ValueError("schema_hash_mismatch")
    requests = payload["requests"]
    if len(requests) != 3 or len({r["request_id"] for r in requests}) != 3:
        raise ValueError("unexpected_requests")
    for request in requests:
        item = {k: v for k, v in request.items() if k != "sha256"}
        if digest(canonical(item)) != request["sha256"]:
            raise ValueError("request_hash_mismatch")
        valid_day(request["information_cutoff"])
        ids = set()
        for source in request["sources"]:
            if source["source_id"] in ids:
                raise ValueError("duplicate_source_id")
            ids.add(source["source_id"])
            if source["history_id"] != request["history_id"]:
                raise ValueError("cross_history_source")
            valid_day(source["operational_available_at"])
            if source["operational_available_at"] > request["information_cutoff"]:
                raise ValueError("future_source")
            if digest(source["text"].encode()) != source["text_sha256"]:
                raise ValueError("source_hash_mismatch")
        if not ids:
            raise ValueError("empty_prefix")
    return {"request_count": len(requests), "request_hashes": [r["sha256"] for r in requests],
            "prompt_sha256": payload["prompt_sha256"], "schema_sha256": payload["schema_canonical_sha256"],
            "model_revision": payload["config"]["model"]["revision"],
            "enabled": payload["config"]["enabled"], "model_executed": False}


def align_evidence(items, sources):
    result = []
    for item in items:
        sid, quote = item["source_id"], item["quote"]
        if sid not in sources:
            raise ValueError("evidence_unknown_source")
        text = sources[sid]["text"]
        first = text.find(quote)
        if not quote or first < 0 or text.find(quote, first + 1) >= 0:
            raise ValueError("evidence_quote_not_unique_exact_match")
        result.append({"source_id": sid, "start": first, "end": first + len(quote), "quote": quote})
    return result


def validate_bounds(candidate):
    numbers = {}
    for endpoint in ("start", "end"):
        bounds = candidate[endpoint]
        lower, upper = bounds["lower"], bounds["upper"]
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("reversed_endpoint_bounds")
        precision = bounds["precision"]
        if precision == "exact_day" and (lower is None or lower != upper):
            raise ValueError("invalid_exact_day")
        if precision in {"year_only", "month_only"}:
            if lower is None or upper is None:
                raise ValueError("missing_partial_date_bounds")
            lo, hi = date.fromisoformat(lower), date.fromisoformat(upper)
            if precision == "year_only" and not (lo.year == hi.year and lo.month == 1 and lo.day == 1 and hi.month == 12 and hi.day == 31):
                raise ValueError("invalid_year_bounds")
            if precision == "month_only":
                import calendar
                if not (lo.year == hi.year and lo.month == hi.month and lo.day == 1 and hi.day == calendar.monthrange(hi.year, hi.month)[1]):
                    raise ValueError("invalid_month_bounds")
        numbers[endpoint] = [date.fromisoformat(lower).toordinal() if lower else -math.inf,
                             date.fromisoformat(upper).toordinal() if upper else math.inf]
    sl, su = numbers["start"]
    el, eu = numbers["end"]
    observed = candidate["state_observed_at"]
    if len(set(observed)) != len(observed):
        raise ValueError("duplicate_state_observation")
    if observed:
        days = [date.fromisoformat(day).toordinal() for day in observed]
        su, el = min(su, min(days)), max(el, max(days) + 1)
    if sl > min(su, eu - 1) or max(el, sl + 1) > eu:
        raise ValueError("inconsistent_temporal_interval")
    if candidate["modality"] == "announced_future" and observed:
        raise ValueError("plan_with_actual_state_observation")


def validate_output(raw, request, schema):
    value = strict_json(raw)
    validate_schema(value, schema)
    sources = {s["source_id"]: s for s in request["sources"]}
    aligned = {"candidates": [], "aliases": [], "unresolved": []}
    for group, id_key in (("candidates", "candidate_id"), ("aliases", "alias_id")):
        ids = [item[id_key] for item in value[group]]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_" + id_key)
    for candidate in value["candidates"]:
        sid = candidate["source_id"]
        if sid not in sources:
            raise ValueError("candidate_unknown_source")
        spans = align_evidence(candidate["evidence"], sources)
        if sid not in {span["source_id"] for span in spans}:
            raise ValueError("candidate_missing_primary_evidence")
        validate_bounds(candidate)
        report = candidate["reported_at"]
        horizon = report or sources[sid]["operational_available_at"]
        if report is not None and report > sources[sid]["operational_available_at"]:
            raise ValueError("report_after_source_availability")
        if candidate["modality"] == "reported_actual" and any(day > horizon for day in candidate["state_observed_at"]):
            raise ValueError("observation_after_report")
        aligned["candidates"].append({**candidate, "aligned_evidence": spans,
            "context_source_ids": sorted(s for s in sources if s != sid),
            "validation_scope": "schema_exact_spans_and_temporal_consistency_not_entailment",
            "selected_for_materialization": False})
    for group in ("aliases", "unresolved"):
        for item in value[group]:
            aligned[group].append({**item, "aligned_evidence": align_evidence(item["evidence"], sources),
                                   "context_source_ids": sorted(sources)})
    return aligned


def emit_transport(result):
    raw = canonical(result)
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError("result_payload_exceeds_hard_cap")
    encoded = base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")
    if len(encoded) > MAX_ENCODED_BYTES:
        raise ValueError("encoded_payload_exceeds_hard_cap")
    chunks = [encoded[i:i + CHUNK_CHARS] for i in range(0, len(encoded), CHUNK_CHARS)]
    for number, chunk in enumerate(chunks, 1):
        print("MODEL_PILOT_CHUNK " + json.dumps({"index": number, "total": len(chunks), "data": chunk}, separators=(",", ":")), flush=True)
    print("MODEL_PILOT_COMPLETE " + json.dumps({"chunks": len(chunks), "sha256": digest(raw),
          "json_bytes": len(raw), "encoded_bytes": len(encoded)}, separators=(",", ":")), flush=True)


def inference(payload):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from huggingface_hub import snapshot_download
    config = payload["config"]
    model_config = config["model"]
    revision = model_config["revision"]
    if revision != model_config["tokenizer_revision"]:
        raise ValueError("tokenizer_revision_differs")
    torch.manual_seed(config["runtime"]["seed"])
    started = time.monotonic()
    snapshot = Path(snapshot_download(model_config["repo_id"], revision=revision,
        allow_patterns=["*.json", "*.txt", "*.safetensors", "LICENSE", "README.md"], token=False))
    assets = []
    for asset in sorted(snapshot.rglob("*")):
        if asset.is_file():
            h = hashlib.sha256()
            with asset.open("rb") as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    h.update(block)
            assets.append({"path": str(asset.relative_to(snapshot)), "bytes": asset.stat().st_size, "sha256": h.hexdigest()})
    tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(snapshot, local_files_only=True,
        trust_remote_code=False, use_safetensors=True, torch_dtype=torch.bfloat16,
        device_map=0, attn_implementation="sdpa")
    model.eval()
    metadata = {"python": platform.python_version(), "platform": platform.platform(),
        "resolved_packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions() if d.metadata.get("Name")},
        "model_revision": revision, "asset_hashes": assets, "torch_cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0), "immutable_full_environment_claimed": False,
        "container_digest": config["runtime"].get("container_digest"),
        "generation_config": config["runtime"]["generation"], "batch_size": 1,
        "schema_canonical_sha256": payload["schema_canonical_sha256"],
        "prompt_sha256": payload["prompt_sha256"], "job_id": None,
        "job_id_note": "Bind externally from confirmed submission response; no environment dump."}
    outputs = []
    for request in payload["requests"]:
        messages = [{"role": "system", "content": payload["prompt"]},
                    {"role": "user", "content": "OUTPUT_SCHEMA\n" + canonical(payload["output_schema"]).decode() +
                     "\nSOURCE_REQUEST\n" + canonical(request).decode()}]
        rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        encoded = tokenizer(rendered, return_tensors="pt", add_special_tokens=False, truncation=False)
        input_ids = encoded["input_ids"][0].tolist()
        entry = {"request_id": request["request_id"], "request_sha256": request["sha256"],
                 "rendered_template_sha256": digest(rendered.encode()), "input_token_ids_sha256": digest(canonical(input_ids)),
                 "input_token_ids": input_ids, "input_tokens": len(input_ids), "raw_output_text": None,
                 "validated_candidates": None, "status": "not_generated"}
        if len(input_ids) > 12288:
            entry.update(status="input_token_guard_failed", error="over_12288_tokens_no_truncation")
            outputs.append(entry)
            break
        try:
            torch.cuda.reset_peak_memory_stats()
            start = time.monotonic()
            with torch.inference_mode():
                generated = model.generate(**encoded.to(model.device), do_sample=False, num_beams=1,
                    max_new_tokens=4096, repetition_penalty=1.0, use_cache=True)
            ids = generated[0][len(input_ids):].tolist()
            raw = tokenizer.decode(ids, skip_special_tokens=True, clean_up_tokenization_spaces=False)
            entry.update(raw_output_text=raw, raw_output_sha256=digest(raw.encode()), generated_token_ids=ids,
                         generated_tokens=len(ids), wall_time_seconds=time.monotonic()-start,
                         peak_memory_bytes=torch.cuda.max_memory_allocated())
            eos = model.generation_config.eos_token_id
            eos = [eos] if isinstance(eos, int) else (eos or [])
            finished = bool(ids and ids[-1] in eos)
            entry["termination_reason"] = "eos" if finished else "length_or_non_eos_stop"
            if not finished:
                entry.update(status="generation_termination_failed", error="no_eos_no_candidate_admission")
            else:
                try:
                    entry["validated_candidates"] = validate_output(raw, request, payload["output_schema"])
                    entry["status"] = "validated_candidate_output_not_selected_claims"
                except (ValueError, TypeError, KeyError) as error:
                    entry.update(status="parse_schema_or_evidence_failure", error=str(error)[:250])
        except Exception as error:
            entry.update(status="generation_failed", error_type=type(error).__name__)
            outputs.append(entry)
            break
        outputs.append(entry)
    return {"run_status": "completed" if len(outputs)==3 and all(x["status"]=="validated_candidate_output_not_selected_claims" for x in outputs) else "partial_or_failed",
            "metadata": metadata, "outputs": outputs, "total_wall_time_seconds": time.monotonic()-started,
            "no_automatic_retries": True, "model_executed": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-model", action="store_true")
    parser.add_argument("--transport-probe", action="store_true")
    args = parser.parse_args()
    if PAYLOAD_B64 == "__MODEL_PILOT_" + "PAYLOAD__":
        raise SystemExit("Use prepare_model_pilot.py to embed the verified inputs first.")
    payload = strict_json(gzip.decompress(base64.b64decode(PAYLOAD_B64)).decode())
    check = verify_payload(payload)
    if args.transport_probe:
        if args.run_model:
            raise SystemExit("Probe and inference flags cannot be combined.")
        emit_transport({"probe": True, "verification": check, "model_executed": False})
    elif args.run_model:
        if payload["config"].get("enabled") is not True:
            raise SystemExit("Model execution disabled by embedded config.")
        try:
            emit_transport(inference(payload))
        except Exception as error:
            emit_transport({"run_status": "fatal_failure", "error_type": type(error).__name__,
                            "model_executed": "unknown_or_partial", "outputs": []})
            raise SystemExit(1)
    else:
        print(json.dumps(check, indent=2))


if __name__ == "__main__":
    main()

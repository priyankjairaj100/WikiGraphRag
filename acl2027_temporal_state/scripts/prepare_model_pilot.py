#!/usr/bin/env python3
"""Build a self-contained job script locally. Never submit a job or use network.

python scripts/prepare_model_pilot.py --dry-run
python scripts/prepare_model_pilot.py --output /path/to/prepared_job.py
python scripts/prepare_model_pilot.py --decode-log /path/to/log.txt --decoded-output /path/to/result.json
"""
import argparse
import base64
import gzip
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/model_pilot_job_template.py"
spec = importlib.util.spec_from_file_location("model_pilot_template", TEMPLATE)
template = importlib.util.module_from_spec(spec)
spec.loader.exec_module(template)


def build_payload():
    config = template.strict_json((ROOT / "configs/model_backend_pilot.json").read_text())
    prompt_path = ROOT / "configs/extractor_prompt_v03.txt"
    schema_path = ROOT / "configs/extractor_output_schema_v03.json"
    prompt = prompt_path.read_text()
    schema = template.strict_json(schema_path.read_text())
    expected_prompt = config["input"].get("prompt_sha256")
    expected_schema = config["input"].get("output_schema_sha256")
    if expected_prompt is not None and expected_prompt != template.digest(prompt_path.read_bytes()):
        raise ValueError("configured_prompt_hash_mismatch")
    if expected_schema is not None and expected_schema != template.digest(schema_path.read_bytes()):
        raise ValueError("configured_schema_file_hash_mismatch")
    requests = []
    pack_manifest = []
    allowed_source_fields = {"source_id", "history_id", "text_sha256", "reported_publication_date",
                             "operational_available_at", "availability_basis", "text"}
    for pack in config["input"]["source_packs"]:
        path = ROOT / pack["path"]
        raw = path.read_bytes()
        if template.digest(raw) != pack["sha256"]:
            raise ValueError("source_pack_hash_mismatch")
        content = template.strict_json(raw.decode())
        if set(content) != {"purpose", "prefixes", "sources"}:
            raise ValueError("unexpected_source_pack_fields")
        if content["prefixes"] != pack["history_prefixes"]:
            raise ValueError("source_prefix_metadata_mismatch")
        for source in content["sources"]:
            if set(source) != allowed_source_fields:
                raise ValueError("unexpected_source_fields")
            template.valid_day(source["reported_publication_date"])
        for history, cutoff in sorted(content["prefixes"].items()):
            sources = sorted((dict(source) for source in content["sources"] if source["history_id"] == history),
                             key=lambda source: (source["operational_available_at"], source["source_id"]))
            request = {"request_id": history + "__" + cutoff, "history_id": history,
                       "information_cutoff": cutoff, "sources": sources}
            request["sha256"] = template.digest(template.canonical(request))
            requests.append(request)
        pack_manifest.append({"path": pack["path"], "sha256": pack["sha256"]})
    payload = {"config": config, "prompt": prompt, "prompt_sha256": template.digest(prompt.encode()),
               "output_schema": schema, "schema_canonical_sha256": template.digest(template.canonical(schema)),
               "schema_file_sha256": template.digest(schema_path.read_bytes()),
               "source_pack_manifest": pack_manifest, "requests": sorted(requests, key=lambda x: x["request_id"]),
               "builder_version": "source_only_v0.3", "runtime_environment_locked": False}
    template.verify_payload(payload)
    return payload


def decode_transport(path):
    chunks, footer = {}, None
    total = None
    for line in path.read_text().splitlines():
        if line.startswith("MODEL_PILOT_CHUNK "):
            item = template.strict_json(line[len("MODEL_PILOT_CHUNK "):])
            if set(item) != {"index", "total", "data"} or type(item["index"]) is not int or type(item["total"]) is not int:
                raise ValueError("invalid_transport_chunk")
            if item["index"] in chunks or not isinstance(item["data"], str):
                raise ValueError("duplicate_or_invalid_chunk")
            if not 1 <= item["index"] <= item["total"] <= 1000:
                raise ValueError("invalid_chunk_index")
            if total is not None and item["total"] != total:
                raise ValueError("conflicting_chunk_total")
            if len(item["data"]) > template.CHUNK_CHARS:
                raise ValueError("oversized_chunk")
            total = item["total"]
            chunks[item["index"]] = item["data"]
        elif line.startswith("MODEL_PILOT_COMPLETE "):
            if footer is not None:
                raise ValueError("duplicate_completion_footer")
            footer = template.strict_json(line[len("MODEL_PILOT_COMPLETE "):])
    if footer is None or total is None or set(chunks) != set(range(1, total + 1)):
        raise ValueError("incomplete_transport")
    if set(footer) != {"chunks", "sha256", "json_bytes", "encoded_bytes"} or footer["chunks"] != total:
        raise ValueError("invalid_completion_footer")
    encoded = "".join(chunks[i] for i in range(1, total + 1))
    if len(encoded) != footer["encoded_bytes"] or len(encoded) > template.MAX_ENCODED_BYTES:
        raise ValueError("encoded_size_mismatch")
    import io
    compressed = base64.b64decode(encoded, validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(template.MAX_RESULT_BYTES + 1)
    if len(raw) > template.MAX_RESULT_BYTES or len(raw) != footer["json_bytes"]:
        raise ValueError("decoded_size_mismatch")
    if template.digest(raw) != footer["sha256"]:
        raise ValueError("result_digest_mismatch")
    return template.strict_json(raw.decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--decode-log", type=Path)
    parser.add_argument("--decoded-output", type=Path)
    args = parser.parse_args()
    if args.dry_run and args.output:
        parser.error("--dry-run cannot be combined with --output.")
    if args.decode_log:
        if args.output or args.dry_run or not args.decoded_output:
            parser.error("Decoding requires --decoded-output and no preparation flags.")
        result = decode_transport(args.decode_log)
        args.decoded_output.write_bytes(template.canonical(result) + b"\n")
        print(json.dumps({"transport_verified": True, "output": str(args.decoded_output)}))
        return
    if args.decoded_output:
        parser.error("--decoded-output requires --decode-log")
    payload = build_payload()
    summary = template.verify_payload(payload)
    summary["schema_file_sha256"] = payload["schema_file_sha256"]
    summary["payload_sha256"] = template.digest(template.canonical(payload))
    summary["token_count_verified"] = False
    summary["token_count_note"] = "Enforced after pinned tokenizer loading; dry-run uses no model libraries or network."
    if args.output:
        encoded = base64.b64encode(gzip.compress(template.canonical(payload), mtime=0)).decode()
        script = TEMPLATE.read_text().replace('PAYLOAD_B64 = "__MODEL_PILOT_PAYLOAD__"', 'PAYLOAD_B64 = ' + json.dumps(encoded), 1)
        compile(script, str(args.output), "exec")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(script)
        summary["prepared_script"] = str(args.output)
        summary["prepared_script_sha256"] = template.digest(script.encode())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

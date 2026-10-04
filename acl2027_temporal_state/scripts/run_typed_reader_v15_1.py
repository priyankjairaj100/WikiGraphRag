#!/usr/bin/env python3
"""Frozen, source-only typed-reader diagnostic; private values stay external."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CODE = (
    "scripts/run_typed_reader_v15_1.py",
    "scripts/reference_numeric_v15.py",
    "scripts/inventory_inline_xbrl_v14.py",
    "src/temporal_state/typed_reader_v15_1.py",
)
REQUIRED_INPUT = (
    "data/fresh_source_audit_v14/source_identity_structure.json",
    "results/inline_xbrl_link_inventory_v14.json",
)
CANONICAL_DECIMAL = re.compile(r"(?:0|-?[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z|(?:-?0\.[0-9]*[1-9])\Z")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def update_receipt(path, value):
    temporary = Path(str(path) + ".progress")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def confined_path(base, relative):
    base = Path(base).resolve()
    candidate = (base / relative).resolve()
    if Path(relative).is_absolute() or not candidate.is_relative_to(base):
        raise ValueError("path escapes declared root")
    return candidate


def validate_parent_lineage(protocol, root=ROOT):
    """An encoding-only amendment retains and binds the complete failed run."""
    linked = {}
    for kind in ("parent_protocol", "parent_result", "amendment"):
        item = protocol[kind]
        if set(item) != {"path", "sha256"}:
            raise ValueError("parent/amendment binding shape invalid")
        path = confined_path(root, item["path"])
        if file_sha(path) != item["sha256"]:
            raise ValueError("parent/amendment digest mismatch: " + kind)
        if protocol.get("input_bindings", {}).get(item["path"]) != item["sha256"]:
            raise ValueError("parent/amendment must be an input binding: " + kind)
        linked[kind] = path
    if protocol["amendment"]["path"] != "docs/typed_reader_encoding_amendment_v15_1.txt":
        raise ValueError("unexpected encoding amendment document")
    parent = json.loads(linked["parent_protocol"].read_text())
    outcome = json.loads(linked["parent_result"].read_text())
    if parent.get("schema_version") != "typed_reader_execution_protocol_v15":
        raise ValueError("unexpected parent protocol schema")
    if (outcome.get("schema_version") != "typed_reader_execution_v15"
            or outcome.get("status") != "complete_with_technical_failures"
            or not outcome.get("finished_at_utc")
            or outcome.get("protocol_sha256") != protocol["parent_protocol"]["sha256"]
            or len(outcome.get("records", [])) != 12
            or not any(row.get("technical_errors") for row in outcome["records"])):
        raise ValueError("parent must be the completed retained technical-failure attempt")
    if [r["source_path"] for r in outcome["records"]] != [r["source_path"] for r in parent["sources"]]:
        raise ValueError("parent outcome source denominator/order mismatch")
    for field in ("sources", "expected_total_nonfraction_count", "design_sha256", "reference_runtime", "lookup_integration", "natural_QA_predictions"):
        if protocol.get(field) != parent.get(field):
            raise ValueError("encoding amendment changes frozen field: " + field)
    profile = dict(protocol["methods_profile"])
    amendment = profile.pop("encoding_amendment", None)
    if (profile != parent["methods_profile"]
            or amendment != {"scope": "encoding_only", "amendment_sha256": protocol["amendment"]["sha256"]}):
        raise ValueError("method changes exceed the explicitly bound encoding amendment")
    # Preserve every original bound input and implementation/control file.
    # New code/controls are additional; old files remain immutable evidence.
    for name, expected in parent["input_bindings"].items():
        if protocol["input_bindings"].get(name) != expected or file_sha(confined_path(root, name)) != expected:
            raise ValueError("historical input changed or omitted: " + name)
    for name, expected in parent["code_bindings"].items():
        if file_sha(confined_path(root, name)) != expected:
            raise ValueError("historical code changed: " + name)
    for name in ("scripts/reference_numeric_v15.py", "scripts/inventory_inline_xbrl_v14.py"):
        if protocol["code_bindings"].get(name) != parent["code_bindings"].get(name):
            raise ValueError("reference or anchor inventory implementation changed")
    return {"parent_protocol_sha256": protocol["parent_protocol"]["sha256"], "parent_result_sha256": protocol["parent_result"]["sha256"], "amendment_sha256": protocol["amendment"]["sha256"]}


def validate_protocol(protocol, sources, root=ROOT):
    """Hash all code, dependencies and twelve sources before parsing any values."""
    if protocol.get("schema_version") != "typed_reader_execution_protocol_v15_1":
        raise ValueError("unexpected protocol schema")
    if not protocol.get("frozen_at_utc"):
        raise ValueError("protocol has no freeze timestamp")
    validate_parent_lineage(protocol, root)
    codes = protocol.get("code_bindings", {})
    if not set(REQUIRED_CODE).issubset(codes):
        raise ValueError("required execution code binding missing")
    if not set(REQUIRED_INPUT).issubset(protocol.get("input_bindings", {})):
        raise ValueError("historical population binding missing")
    checked = {}
    for collection in (codes, protocol.get("input_bindings", {})):
        for name, expected in collection.items():
            location = confined_path(root, name)
            if file_sha(location) != expected:
                raise ValueError("repository binding digest mismatch: " + name)
            checked[str(location)] = expected
    design = confined_path(root, "docs/typed_reader_study_design_v15.txt")
    if file_sha(design) != protocol.get("design_sha256"):
        raise ValueError("study design digest mismatch")
    runtime = protocol["reference_runtime"]
    runtime_path = Path(runtime["path"]).resolve()
    if not runtime.get("files") or not runtime.get("version"):
        raise ValueError("reference runtime pin missing")
    for name, expected in runtime["files"].items():
        location = confined_path(runtime_path, name)
        if file_sha(location) != expected:
            raise ValueError("runtime binding digest mismatch: " + name)
        checked[str(location)] = expected
    for name, expected in runtime.get("additional_files", {}).items():
        location = Path(name).resolve()
        if not Path(name).is_absolute() or file_sha(location) != expected:
            raise ValueError("additional dependency digest mismatch")
        checked[str(location)] = expected
    for location in (runtime["wheel_path"], *runtime.get("additional_wheels", [])):
        if str(Path(location).resolve()) not in checked:
            raise ValueError("wheel lacks pre-execution digest binding")
    entries = protocol["sources"]
    if len(entries) != 12 or len({x["source_path"] for x in entries}) != 12:
        raise ValueError("source denominator must be twelve distinct objects")
    if len({x["external_filename"] for x in entries}) != 12:
        raise ValueError("duplicate physical source filename")
    if protocol.get("expected_total_nonfraction_count") != 23651:
        raise ValueError("frozen occurrence denominator changed")
    if sum(x["expected_nonfraction_count"] for x in entries) != 23651:
        raise ValueError("per-source occurrence counts do not sum to frozen population")
    if protocol.get("natural_QA_predictions") != 0:
        raise ValueError("natural QA is outside this protocol")
    historical = json.loads(confined_path(root, REQUIRED_INPUT[0]).read_text())["records"]
    inventory = json.loads(confined_path(root, REQUIRED_INPUT[1]).read_text())["records"]
    if len(historical) != 12 or len(inventory) != 12:
        raise ValueError("historical population inventory incomplete")
    inventory_by_name = {x["source_path"]: x for x in inventory}
    for item, original in zip(entries, historical):
        if Path(item["external_filename"]).name != item["external_filename"]:
            raise ValueError("source filename must be a basename")
        if (item["source_path"] != original["source_path"]
                or item["external_filename"] != Path(original["source_path"]).name
                or item["expected_sha256"] != original["source_sha256"]
                or item["expected_bytes"] != original["bytes"]
                or item["expected_nonfraction_count"] != inventory_by_name[item["external_filename"]]["counts"]["nonfraction_facts"]):
            raise ValueError("new protocol changes the historical source population/order/count")
        source = confined_path(sources, item["external_filename"])
        if source.stat().st_size != item["expected_bytes"] or file_sha(source) != item["expected_sha256"]:
            raise ValueError("source integrity mismatch: " + item["source_path"])
    lookup = protocol["lookup_integration"]
    if lookup.get("max_distinct_keys_per_source") != 8 or lookup.get("wrong_source_sha256_probe") is not True:
        raise ValueError("lookup integration protocol changed")
    return checked


def compare_records(reader, reference, expected_count):
    """Include missing/unsupported occurrences; compare only aligned numeric rows.

    Inputs are minimal dictionaries, not raw source content. Public discrepancy
    records deliberately contain neither lexical strings nor normalized values.
    """
    totals = Counter(expected_population=expected_count, reader_observed=len(reader), reference_observed=len(reference))
    reader_statuses, reference_statuses, bindings, fact_statuses = Counter(), Counter(), Counter(), Counter()
    discrepancies = []
    for ordinal in range(max(expected_count, len(reader), len(reference))):
        left = reader[ordinal] if ordinal < len(reader) else None
        right = reference[ordinal] if ordinal < len(reference) else None
        ls = left.get("numeric_status", "missing_status") if left else "missing_occurrence"
        rs = right.get("status", "missing_status") if right else "missing_occurrence"
        reader_statuses[ls] += 1
        reference_statuses[rs] += 1
        bindings[left.get("binding_status", "missing_status") if left else "missing_occurrence"] += 1
        fact_statuses[left.get("status", "missing_status") if left else "missing_occurrence"] += 1
        reason = None
        location = (left or right or {}).get("dom_path")
        if left is None or right is None:
            totals["missing_occurrence_pairs"] += 1
            reason = "missing_occurrence"
        elif not left.get("dom_path") or left["dom_path"] != right.get("dom_path"):
            totals["alignment_failures"] += 1
            reason = "occurrence_locator_mismatch"
        elif ls == rs == "normalized":
            lv, rv = left.get("normalized_value"), right.get("canonical_value")
            if not isinstance(lv, str) or not CANONICAL_DECIMAL.fullmatch(lv) or not isinstance(rv, str) or not CANONICAL_DECIMAL.fullmatch(rv):
                totals["canonical_contract_failures"] += 1
                reason = "normalized_status_without_canonical_decimal"
            else:
                totals["common_normalized_population"] += 1
                if lv == rv:
                    totals["exact_numeric_agreements"] += 1
                else:
                    totals["exact_numeric_disagreements"] += 1
                    reason = "exact_numeric_disagreement"
        elif ls == rs == "nil":
            totals["both_nil_no_numeric_comparison"] += 1
        else:
            totals["outside_common_normalized_population"] += 1
            if ls != rs:
                reason = "numeric_status_difference"
        if reason:
            discrepancies.append({"fact_ordinal": ordinal, "dom_path": location, "reason": reason, "reader_status": ls, "reference_status": rs, "reader_issues": (left or {}).get("issues", []), "reference_reason": (right or {}).get("reason")})
    totals["count_matches_expected"] = int(len(reader) == len(reference) == expected_count)
    return {"counts": dict(totals), "reader_status_counts": dict(reader_statuses), "reference_status_counts": dict(reference_statuses), "binding_status_counts": dict(bindings), "reader_fact_status_counts": dict(fact_statuses), "discrepancies": discrepancies}


def combine_summaries(records):
    combined = {name: Counter() for name in ("counts", "reader_status_counts", "reference_status_counts", "binding_status_counts", "reader_fact_status_counts", "anchor_counts", "lookup_counts")}
    for record in records:
        for name, counter in combined.items():
            counter.update(record.get(name, {}))
    return {name: dict(counter) for name, counter in combined.items()}


def completion_status(records, totals, dependency_errors):
    counts, probes = totals["counts"], totals["lookup_counts"]
    technical = (any(r["technical_errors"] for r in records) or bool(dependency_errors)
                 or counts.get("count_matches_expected", 0) != 12
                 or counts.get("alignment_failures", 0)
                 or counts.get("canonical_contract_failures", 0)
                 or any(r.get("anchor_failures") for r in records)
                 or probes.get("seed_candidate_missing", 0)
                 or probes.get("wrong_source_sha_not_blocked", 0))
    return "complete_with_technical_failures" if technical else "complete_with_disagreements" if counts.get("exact_numeric_disagreements", 0) else "complete"


def verify_anchor(anchor, expected_anchor, data, source_sha):
    if not isinstance(anchor, dict) or not isinstance(expected_anchor, dict):
        return "missing_anchor"
    for key in ("source_sha256", "dom_path", "byte_start", "byte_stop", "span_sha256"):
        if anchor.get(key) != expected_anchor.get(key):
            return "anchor_metadata_mismatch"
    start, stop = anchor.get("byte_start"), anchor.get("byte_stop")
    if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= len(data):
        return "invalid_anchor_bounds"
    if anchor["source_sha256"] != source_sha or hashlib.sha256(data[start:stop]).hexdigest() != anchor["span_sha256"]:
        return "anchor_byte_digest_mismatch"
    return "verified"


def expanded_paths(document):
    """Independent lxml traversal matching expanded-name ordinal locators."""
    root = document.getroot()
    paths = {root: "/" + root.tag + "[1]"}
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        siblings = Counter()
        for child in parent:
            if not isinstance(child.tag, str):
                continue
            siblings[child.tag] += 1
            paths[child] = paths[parent] + "/" + child.tag + "[" + str(siblings[child.tag]) + "]"
    return paths


def expected_anchor_map(data, document, source_sha, inventory):
    spans = inventory.source_spans(data)
    paths = expanded_paths(document)
    result = {}
    for tag in inventory.TAGS:
        nodes = list(document.getroot().iter(tag))
        if len(nodes) != len(spans[tag]):
            raise ValueError("independent DOM versus Expat occurrence count mismatch")
        for node, span in zip(nodes, spans[tag]):
            locator = paths[node]
            result[locator] = {"source_sha256": source_sha, "dom_path": locator, **span}
    return result


def collapsed_xml_token(value):
    """Independent XML Schema ID/IDREF whitespace collapse, not Unicode strip."""
    return None if value is None else re.sub(r"[ \t\r\n]+", " ", value).strip(" ")


def anchor_audit(data, typed, document, source_sha, inventory):
    expected = expected_anchor_map(data, document, source_sha, inventory)
    paths = expanded_paths(document)
    nodes = list(document.getroot().iter("{" + inventory.IX + "}nonFraction"))
    context_nodes, unit_nodes = {}, {}
    for tag, pool in (("context", context_nodes), ("unit", unit_nodes)):
        for node in document.getroot().iter("{" + inventory.XB + "}" + tag):
            pool.setdefault(collapsed_xml_token(node.get("id")), []).append(node)
    counts, failures = Counter(), []
    for ordinal, fact in enumerate(typed["facts"]):
        checks = [("fact", fact.get("anchor"), expected.get(paths[nodes[ordinal]]) if ordinal < len(nodes) else None)]
        if ordinal < len(nodes):
            node = nodes[ordinal]
            evidence = fact.get("evidence_anchors", [])
            for kind, attr, pool in (("context", "contextRef", context_nodes), ("unit", "unitRef", unit_nodes)):
                targets = pool.get(collapsed_xml_token(node.get(attr)), [])
                if len(targets) == 1:
                    target = expected[paths[targets[0]]]
                    matches = [a for a in evidence if a.get("dom_path") == target["dom_path"]]
                    checks.append((kind, matches[0] if len(matches) == 1 else None, target))
                else:
                    counts[kind + "_reference_not_unique"] += 1
        for kind, anchor, target in checks:
            status = verify_anchor(anchor, target, data, source_sha)
            counts[kind + "_anchors_" + status] += 1
            if status != "verified":
                failures.append({"fact_ordinal": ordinal, "dom_path": fact.get("anchor", {}).get("dom_path"), "reason": kind + "_" + status})
        for anchor in fact.get("evidence_anchors", []):
            start, stop = anchor.get("byte_start"), anchor.get("byte_stop")
            valid = (type(start) is int and type(stop) is int and 0 <= start < stop <= len(data)
                     and anchor.get("source_sha256") == source_sha
                     and hashlib.sha256(data[start:stop]).hexdigest() == anchor.get("span_sha256"))
            counts["all_evidence_anchor_bytes_verified" if valid else "all_evidence_anchor_bytes_failed"] += 1
            if not valid:
                failures.append({"fact_ordinal": ordinal, "reason": "evidence_anchor_byte_integrity_failure"})
    return dict(counts), failures


def lookup_integration(typed, implementation, maximum):
    """Source-derived self-consistency only; no financial or QA reference."""
    seen, private, public = set(), [], []
    counts = Counter()
    for fact in typed["facts"]:
        if fact["status"] != "normalized" or fact["binding_status"] != "reported_aspects_resolved":
            continue
        query = implementation.make_reported_query(typed, fact)
        key = json.dumps(query, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        counts["selected_distinct_keys"] += 1
        result = implementation.lookup(typed, query)
        included = fact["fact_ordinal"] in result["candidate_ordinals"]
        counts["seed_candidate_included" if included else "seed_candidate_missing"] += 1
        wrong = dict(query)
        wrong["source_sha256"] = "0" * 64 if query["source_sha256"] != "0" * 64 else "1" * 64
        try:
            wrong_result = implementation.lookup(typed, wrong)
            blocked = False
        except implementation.ReaderError:
            wrong_result, blocked = None, True
        counts["wrong_source_sha_blocked" if blocked else "wrong_source_sha_not_blocked"] += 1
        row = {"seed_ordinal": fact["fact_ordinal"], "seed_candidate_included": included, "wrong_source_sha_blocked": blocked, "lookup_status": result["status"]}
        public.append(row)
        private.append({**row, "query": query, "lookup_result": result, "wrong_source_query": wrong, "wrong_source_result": wrong_result})
        if len(seen) == maximum:
            break
    return dict(counts), public, private


def run_source(entry, sources, external, implementation, reference, inventory, maximum):
    started = time.monotonic()
    data = (sources / entry["external_filename"]).read_bytes()
    # Detect changes since the all-source preflight, before either parser runs.
    if len(data) != entry["expected_bytes"] or hashlib.sha256(data).hexdigest() != entry["expected_sha256"]:
        raise ValueError("source changed after preflight")
    errors, typed, document = [], None, None
    reader, refs, typed_facts = [], [], []
    try:
        typed = implementation.read_inline_xbrl(data, source_version=entry["source_path"] + "@sha256:" + entry["expected_sha256"])
        typed_facts = typed["facts"]
        reader = [{"dom_path": f["anchor"]["dom_path"], "numeric_status": f["numeric_status"], "normalized_value": f["normalized_value"], "binding_status": f["binding_status"], "status": f["status"], "issues": f["issues"]} for f in typed_facts]
    except Exception as error:
        errors.append({"component": "reader", "error_type": type(error).__name__, "external_error": str(error)})
    try:
        document = reference.parse_document(data)
        paths = expanded_paths(document)
        for node in reference.iter_nonfractions(document):
            try:
                converted = reference.normalize_nonfraction(node, include_private=True)
            except Exception as error:
                converted = {"status": "adapter_exception", "canonical_value": None, "error_type": type(error).__name__, "_external_error": str(error)}
            refs.append({"dom_path": paths[node], "xml_xpath": document.getpath(node), **converted})
    except Exception as error:
        errors.append({"component": "reference", "error_type": type(error).__name__, "external_error": str(error)})
    public = {"source_path": entry["source_path"], "source_sha256": entry["expected_sha256"], "bytes": len(data), **compare_records(reader, refs, entry["expected_nonfraction_count"])}
    public["technical_errors"] = [{k: v for k, v in error.items() if k != "external_error"} for error in errors]
    public["anchor_counts"], public["anchor_failures"] = {}, []
    public["lookup_counts"], public["lookup_probes"] = {}, []
    private_probes = []
    if typed is not None and document is not None:
        try:
            public["anchor_counts"], public["anchor_failures"] = anchor_audit(data, typed, document, entry["expected_sha256"], inventory)
        except Exception as error:
            errors.append({"component": "anchor_audit", "error_type": type(error).__name__, "external_error": str(error)})
            public["technical_errors"].append({"component": "anchor_audit", "error_type": type(error).__name__})
    if typed is not None:
        try:
            public["lookup_counts"], public["lookup_probes"], private_probes = lookup_integration(typed, implementation, maximum)
        except Exception as error:
            errors.append({"component": "lookup_integration", "error_type": type(error).__name__, "external_error": str(error)})
            public["technical_errors"].append({"component": "lookup_integration", "error_type": type(error).__name__})
    destination = external / (Path(entry["external_filename"]).stem + ".diagnostic.jsonl")
    with destination.open("x", encoding="utf-8") as stream:
        def emit(value):
            stream.write(json.dumps(value, sort_keys=True) + "\n")
        emit({"record_type": "source", "source": entry, "errors": errors, "reader_metadata": {k: v for k, v in (typed or {}).items() if k not in ("facts", "contexts", "units")}})
        for kind in ("contexts", "units"):
            for row in (typed or {}).get(kind, []):
                emit({"record_type": kind, "record": row})
        for ordinal in range(max(entry["expected_nonfraction_count"], len(typed_facts), len(refs))):
            emit({"record_type": "fact_pair", "fact_ordinal": ordinal, "typed": typed_facts[ordinal] if ordinal < len(typed_facts) else None, "reference": refs[ordinal] if ordinal < len(refs) else None})
        for row in private_probes:
            emit({"record_type": "lookup_self_consistency", **row})
    public["external_records"] = {"path": str(destination), "bytes": destination.stat().st_size, "sha256": file_sha(destination)}
    public["elapsed_seconds"] = time.monotonic() - started
    public["status"] = "completed" if not public["technical_errors"] else "completed_with_technical_errors"
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--external-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.external_output.exists():
        raise SystemExit("Refusing to overwrite prior attempt/output")
    if args.external_output.resolve().is_relative_to(ROOT):
        raise SystemExit("Source-bearing records must be outside repository")
    protocol = json.loads(args.protocol.read_text())
    args.external_output.mkdir(parents=True)
    result = {"schema_version": "typed_reader_execution_v15_1", "started_at_utc": utc_now(), "status": "preflight", "protocol_path": str(args.protocol), "protocol_sha256": file_sha(args.protocol), "script_sha256": file_sha(Path(__file__)), "source_denominator": 12, "expected_nonfraction_denominator": 23651, "natural_QA_predictions": 0, "model_calls": 0, "records": [], "parent_protocol": protocol.get("parent_protocol"), "parent_result": protocol.get("parent_result"), "amendment": protocol.get("amendment")}
    dump_new(args.output, result)
    dump_new(args.external_output / "attempt_started.json", result)
    try:
        bindings = validate_protocol(protocol, args.sources)
        # Imports of either new reader occur only after every declared binding
        # and every source object has passed the all-source preflight.
        sys.path.insert(0, str(ROOT / "src"))
        sys.path.insert(0, str(ROOT / "scripts"))
        sys.path.insert(0, protocol["reference_runtime"]["path"])
        from temporal_state import typed_reader_v15_1 as implementation
        import reference_numeric_v15 as reference
        import inventory_inline_xbrl_v14 as inventory
        if protocol["reference_runtime"]["version"] != reference.ARELLE_VERSION:
            raise ValueError("reference runtime version differs from adapter pin")
        # Force the pinned official registry import before processing values.
        reference._registry()
    except Exception as error:
        result.update(status="preflight_rejected", finished_at_utc=utc_now(), error_type=type(error).__name__, reason=str(error))
        update_receipt(args.output, result)
        dump_new(args.external_output / "attempt_failed.json", result)
        raise SystemExit(1)
    result["preflight"] = {"status": "passed", "bound_files_verified": len(bindings), "source_objects_verified": 12}
    result["status"] = "running"
    update_receipt(args.output, result)
    for entry in protocol["sources"]:
        try:
            row = run_source(entry, args.sources, args.external_output, implementation, reference, inventory, protocol["lookup_integration"]["max_distinct_keys_per_source"])
        except Exception as error:
            row = {"source_path": entry["source_path"], "source_sha256": entry["expected_sha256"], "status": "source_execution_failed", **compare_records([], [], entry["expected_nonfraction_count"]), "technical_errors": [{"component": "source_execution", "error_type": type(error).__name__}]}
            dump_new(args.external_output / (Path(entry["external_filename"]).stem + ".failure.json"), {"public": row, "external_error": str(error)})
        result["records"].append(row)
        update_receipt(args.output, result)
        print(json.dumps({"source": entry["source_path"], "status": row["status"], "counts": row["counts"]}), flush=True)
        gc.collect()
    result["totals"] = combine_summaries(result["records"])
    result["dependency_validation_errors"] = []
    runtime = protocol["reference_runtime"]
    try:
        dependencies = reference.dependency_receipt(runtime["wheel_path"], additional_wheels=runtime.get("additional_wheels", []))
        result["runtime_dependency_receipt"] = dependencies
        for item in dependencies["imported_files"]:
            if bindings.get(str(Path(item["path"]).resolve())) != item["sha256"]:
                result["dependency_validation_errors"].append({"path": item["path"], "reason": "imported_dependency_not_prebound_or_changed"})
    except Exception as error:
        result["dependency_validation_errors"].append({"error_type": type(error).__name__, "reason": "post_run_dependency_receipt_failed"})
    result["status"] = completion_status(result["records"], result["totals"], result["dependency_validation_errors"])
    result["finished_at_utc"] = utc_now()
    result["interpretation_limits"] = [
        "Every frozen nonFraction occurrence remains in the denominator; unsupported/reference-unsupported/nil rows are not numeric agreements.",
        "The Arelle comparator invokes pinned numeric transformations only and does not load or validate a DTS/taxonomy.",
        "Anchor checks use the historical v14 Expat source-span helper, an algorithm family also used by the reader; lxml supplies a separate DOM traversal. This is not an independent implementation of every anchoring step.",
        "Lookup checks select at most eight distinct normalized, binding-resolved source-derived keys per file in occurrence order. They check integration self-consistency, not natural QA or independent financial gold.",
        "Public records contain counts, failure locators and file digests only; source values and typed records remain external.",
        "Numeric agreement is not verified source identity, visual grounding, consolidated scope, semantic duplicate equivalence or answer accuracy.",
    ]
    update_receipt(args.output, result)
    dump_new(args.external_output / "attempt_finished.json", {"status": result["status"], "finished_at_utc": result["finished_at_utc"], "public_output": str(args.output), "public_output_sha256": file_sha(args.output), "protocol_sha256": result["protocol_sha256"]})
    print(json.dumps({"status": result["status"], "output": str(args.output), "sha256": file_sha(args.output), "totals": result["totals"]["counts"]}), flush=True)


if __name__ == "__main__":
    main()

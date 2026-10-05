#!/usr/bin/env python3
"""Audit all v22 representative conflicts against newly recovered exact sources.

Public output contains derived facts and byte/hash locators, never source text.
All raw text, parsed caches and review excerpts remain in the external directory.
The interval screen is a declared diagnostic, not taxonomy validation or QA gold.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from temporal_state.typed_reader_v15_1 import _parse, read_inline_xbrl

PROTOCOL = ROOT / "data/empirical_v23/source_audit_protocol.json"
RESULT = ROOT / "results/source_audit_v23.json"
EXTERNAL = Path("/workspace/scratch/bdef663e3dfc/wikigraph_v23_external")
ASPECTS = ("concept", "entity", "period", "unit", "dimensions")
ARCHIVE = "6618442537ebeb03cd69327eed9f9a0999d53ab2"


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write("\n")


def historical(path):
    return subprocess.check_output(["git", "show", ARCHIVE + ":acl2027_temporal_state/" + path], cwd=ROOT)


def freeze():
    previous = ROOT / "data/typed_reader_v15/source_recovery_protocol.json"
    write_new(PROTOCOL, {
        "schema": "source_audit_protocol_v23", "frozen_at_utc": now(),
        "script_sha256": file_digest(__file__),
        "input_bindings": {str(path.relative_to(ROOT)): file_digest(path) for path in (
            previous, ROOT / "data/empirical_v23/source_recovery_protocol.json",
            ROOT / "src/temporal_state/typed_reader_v15_1.py")},
        "historical_commit": ARCHIVE,
        "historical_blobs": {path: digest(historical(path)) for path in (
            "src/temporal_state/typed_binding_v22.py", "results/typed_binding_census_v22.json",
            "results/typed_binding_followup_v22.json")},
        "files": json.loads(previous.read_text())["files"],
        "selection": "Every eligible full binding with more than one signed normalized value. Separately mark all 41 v22 first-fact-per-multi-class-concept representative probes. No replacement or conflict selection by outcome.",
        "eligibility": "Exactly v22 normalized, all five reported aspects resolved, visible-markup profile; rendering remains unverified.",
        "diagnostic_rules": {
            "rounding_interval": "For finite decimals d, [value - 0.5*10^(-d), value + 0.5*10^(-d)]. INF is a point. Finite precision and missing accuracy remain unclassified. All intervals use signed exact Decimal arithmetic. Closed endpoints form a conservative screening rule, not an XBRL validity assertion.",
            "signed_gap": "Report zero-involved and mixed-sign flags separately; never discard zero or take absolute values before testing sign.",
            "placement": "Record nearest table, row, paragraph or div anchors and hashes. External review file retains excerpts; public results omit excerpts.",
            "task_admission": "A conflicting binding is not a task. No candidate is admitted without natural question intent and independent source adjudication.",
            "cross_filing": "Compute exact and namespace-erased local-name diagnostic overlap for all six fixed issuer pairs; inspect schema references. Namespace erasure does not authorize a join. No taxonomy or label equivalence is presumed.",
        },
        "external_directory": str(EXTERNAL), "model_calls": 0,
        "public_release": "Derived numeric/aspect metadata and byte/hash locators only. No raw source excerpts.",
        "held_out": False,
    })
    print(json.dumps({"protocol_sha256": file_digest(PROTOCOL)}))


def eligible(document):
    return [f for f in document["facts"] if f.get("binding_status") == "reported_aspects_resolved"
            and f.get("status") == "normalized" and all(f["resolved_aspects"].get(k) for k in ASPECTS)
            and f.get("visibility") != "hidden_markup"]


def local(value):
    return value.split("}", 1)[1] if isinstance(value, str) and value.startswith("{") and "}" in value else value


def key(aspects, local_names=False):
    if not local_names:
        return canon({k: aspects[k] for k in ASPECTS})
    u = aspects["unit"]
    return canon({"concept": local(aspects["concept"]), "entity": aspects["entity"], "period": aspects["period"],
                  "unit_shape": u.get("shape"), "unit_locals": [local(x) for x in u.get("measures", [])],
                  "unit_numerator_locals": [local(x) for x in u.get("numerator_measures", [])],
                  "unit_denominator_locals": [local(x) for x in u.get("denominator_measures", [])],
                  "dimensions": [(local(x.get("dimension")), local(x.get("member")), x.get("kind"), x.get("placement"))
                                 for x in aspects["dimensions"]]})


def interval(fact):
    decimals = fact["accuracy"].get("decimals")
    precision = fact["accuracy"].get("precision")
    value = Decimal(fact["normalized_value"])
    if decimals == "INF" or precision == "INF":
        return value, value
    if decimals is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 12000
        radius = Decimal("0.5") * (Decimal(10) ** -int(decimals))
        return value - radius, value + radius


def locator(node, source):
    return {"byte_start": node.start, "byte_stop": node.stop,
            "span_sha256": digest(source[node.start:node.stop]), "dom_path": node.path}


def placement(node, source, table_ordinals):
    ancestors = list(node.ancestors())
    table = next((n for n in ancestors if local(n.tag) == "table"), None)
    row = next((n for n in ancestors if local(n.tag) == "tr"), None)
    prose = next((n for n in ancestors if local(n.tag) in ("p", "div")), None)
    visible = row or prose or node
    text = " ".join(visible.text().split())
    public = {"kind": "table" if table else "prose", "table_ordinal": table_ordinals.get(table.start) if table else None,
              "row_or_prose_anchor": locator(visible, source), "text_sha256": digest(text.encode())}
    return public, text[:1600]


def audit_document(item):
    filename = item["external_filename"]
    source = (EXTERNAL / filename).read_bytes()
    if len(source) != item["expected_bytes"] or digest(source) != item["expected_sha256"]:
        raise ValueError("source integrity mismatch: " + filename)
    document = read_inline_xbrl(source, source_version=filename)
    write_new(EXTERNAL / (filename + ".parsed.json"), document)
    nodes = _parse(source)
    starts = {n.start: n for n in nodes}
    table_ordinals = {n.start: i for i, n in enumerate(n for n in nodes if local(n.tag) == "table")}
    facts = eligible(document)
    groups, concepts = defaultdict(list), defaultdict(list)
    for f in facts:
        groups[key(f["reported_aspects"])] .append(f)
        concepts[canon(f["reported_aspects"]["concept"])] .append(f)
    primary = set()
    for members in concepts.values():
        classes = {(key(f["reported_aspects"]), f["normalized_value"]) for f in members}
        if len(classes) > 1:
            first_key = key(members[0]["reported_aspects"])
            if len({f["normalized_value"] for f in groups[first_key]}) > 1:
                primary.add(first_key)
    conflicts, excerpts = [], []
    for binding, members in groups.items():
        values = sorted({Decimal(f["normalized_value"]) for f in members})
        if len(values) < 2:
            continue
        intervals = [interval(f) for f in members]
        comparable = all(v is not None for v in intervals)
        overlap = comparable and max(v[0] for v in intervals) <= min(v[1] for v in intervals)
        strict = comparable and max(v[0] for v in intervals) < min(v[1] for v in intervals)
        identifier = digest((filename + "\n" + binding).encode())[:20]
        records = []
        for fact, bounds in zip(members, intervals):
            node = starts[fact["anchor"]["byte_start"]]
            place, excerpt = placement(node, source, table_ordinals)
            records.append({"fact_ordinal": fact["fact_ordinal"], "normalized_value": fact["normalized_value"],
                            "context_id": fact["context_id"], "unit_id": fact["unit_id"],
                            "scale": fact["scale_property"], "sign_attribute": fact["attributes"].get("sign"),
                            "accuracy": fact["accuracy"], "anchor": fact["anchor"],
                            "evidence_anchors": fact["evidence_anchors"], "placement": place,
                            "interval": [str(x) for x in bounds] if bounds else None,
                            "lexical_sha256": digest(fact["lexical_text"].encode()),
                            "nested_occurrence": fact["nested_occurrence"]})
            excerpts.append({"probe_id": identifier, "primary": binding in primary, "filename": filename,
                             "concept": local(fact["concept"]), "ordinal": fact["fact_ordinal"],
                             "value": fact["normalized_value"], "accuracy": fact["accuracy"],
                             "placement": place["kind"], "table": place["table_ordinal"], "text": excerpt})
        classification = "rounding_interval_overlap" if strict else "rounding_endpoint_only" if overlap else "accuracy_unclassified" if not comparable else "disjoint_declared_accuracy"
        conflicts.append({"probe_id": identifier, "is_v22_representative_probe": binding in primary,
                          "source_version": filename, "source_sha256": digest(source), "binding": json.loads(binding),
                          "distinct_signed_values": [str(x) for x in values], "zero_involved": Decimal(0) in values,
                          "mixed_sign": min(values) < 0 < max(values), "classification": classification,
                          "facts": records, "source_task_admitted": False,
                          "placement_kinds": sorted({f["placement"]["kind"] for f in records}),
                          "table_count": len({f["placement"]["table_ordinal"] for f in records if f["placement"]["kind"] == "table"}),
                          "scale_varies": len({f["scale"] for f in records}) > 1,
                          "context_ids_vary": len({f["context_id"] for f in records}) > 1})
    schema_refs = sorted({n.attrs.get("{http://www.w3.org/1999/xlink}href") for n in nodes if local(n.tag) == "schemaRef"} - {None})
    namespaces = sorted({f["concept"].split("}", 1)[0][1:] for f in facts})
    return document, {"filename": filename, "source_sha256": digest(source), "eligible_facts": len(facts),
                      "representative_conflicts": len(primary), "all_conflict_bindings": len(conflicts),
                      "schema_refs": schema_refs, "concept_namespaces": namespaces,
                      "taxonomy_resources_recovered": 0, "conflicts": conflicts}, excerpts


def cross_pair(left, right):
    def index(doc, local_names):
        result = defaultdict(list)
        for f in eligible(doc):
            result[key(f["reported_aspects"], local_names)].append(f)
        return result
    le, re = index(left, False), index(right, False)
    ll, rl = index(left, True), index(right, True)
    shared = sorted(set(ll) & set(rl))
    different = [k for k in shared if not ({f["normalized_value"] for f in ll[k]} == {f["normalized_value"] for f in rl[k]} and len({f["normalized_value"] for f in ll[k]}) == 1)]
    return {"first": left["source_version"], "second": right["source_version"],
            "exact_shared_bindings": len(set(le) & set(re)), "local_name_diagnostic_shared": len(shared),
            "local_name_diagnostic_not_identical_singleton": len(different), "admitted_joins": 0,
            "admission_blocker": "No cross-version concept/dimension correspondence with schema-definition and label/reference evidence has been independently validated.",
            "predeclared_candidate_order": "First ten lexical local-binding keys with nonidentical singleton values; diagnostic only.",
            "diagnostic_candidates": [{"diagnostic_key_sha256": digest(k.encode()),
                                       "concept_local": json.loads(k)["concept"],
                                       "first_concepts": sorted({f["concept"] for f in ll[k]}),
                                       "second_concepts": sorted({f["concept"] for f in rl[k]}),
                                       "first_ordinals": [f["fact_ordinal"] for f in ll[k]],
                                       "second_ordinals": [f["fact_ordinal"] for f in rl[k]]}
                                      for k in different[:10]]}


def run():
    protocol = json.loads(PROTOCOL.read_text())
    if RESULT.exists():
        raise SystemExit("Audit result exists; create a separately versioned amendment")
    assert file_digest(__file__) == protocol["script_sha256"]
    for path, expected in protocol["input_bindings"].items():
        assert file_digest(ROOT / path) == expected, path
    for path, expected in protocol["historical_blobs"].items():
        assert digest(historical(path)) == expected, path
    recovery = json.loads((ROOT / "results/source_recovery_v23.json").read_text())
    assert recovery["recovered_count"] == 12
    began = now()
    parsed, documents, excerpts = [], [], []
    for item in protocol["files"]:
        document, summary, review = audit_document(item)
        parsed.append(document)
        documents.append(summary)
        excerpts.extend(review)
        print(json.dumps({"filename": summary["filename"], "representative_conflicts": summary["representative_conflicts"],
                          "all_conflict_bindings": summary["all_conflict_bindings"]}), flush=True)
    all_conflicts = [c for d in documents for c in d["conflicts"]]
    primary = [c for c in all_conflicts if c["is_v22_representative_probe"]]
    assert len(primary) == 41, "Historical representative selection did not reconcile"
    assert len(all_conflicts) == 87, "Historical full conflict count did not reconcile"
    pairs = [cross_pair(parsed[i], parsed[i+1]) for i in range(0, 12, 2)]
    write_new(EXTERNAL / "source_audit_review_excerpts.json", excerpts)
    value = {"schema": "source_audit_v23", "started_at_utc": began, "finished_at_utc": now(),
             "protocol_sha256": file_digest(PROTOCOL), "script_sha256": file_digest(__file__),
             "recovery_receipt_sha256": file_digest(ROOT / "results/source_recovery_v23.json"),
             "runtime": {"python": sys.version, "platform": platform.platform()},
             "command": "python3 scripts/audit_sources_v23.py run", "documents": documents,
             "cross_filing_pairs": pairs,
             "totals": {"files": len(documents), "eligible_facts": sum(d["eligible_facts"] for d in documents),
                        "v22_representative_conflicts_recovered": len(primary), "all_conflict_bindings": len(all_conflicts),
                        "primary_classification": dict(Counter(c["classification"] for c in primary)),
                        "all_classification": dict(Counter(c["classification"] for c in all_conflicts)),
                        "mixed_sign_conflicts": sum(c["mixed_sign"] for c in all_conflicts),
                        "zero_involved_conflicts": sum(c["zero_involved"] for c in all_conflicts),
                        "exact_cross_filing_shared": sum(p["exact_shared_bindings"] for p in pairs),
                        "diagnostic_cross_filing_shared": sum(p["local_name_diagnostic_shared"] for p in pairs),
                        "diagnostic_cross_filing_nonidentical": sum(p["local_name_diagnostic_not_identical_singleton"] for p in pairs),
                        "source_tasks_admitted": 0, "model_calls": 0, "held_out_items": 0},
             "limitations": ["Accuracy intervals screen rounding compatibility; they do not prove intended values or taxonomy validity.",
                             "Source text review and natural question design are separate from this deterministic extraction receipt.",
                             "Namespace-erased keys are diagnostics only; no cross-filing identity is admitted.",
                             "Rendering, source semantics and external coverage are not established by markup parsing."]}
    write_new(RESULT, value)
    print(json.dumps(value["totals"], sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("freeze", "run"))
    globals()[p.parse_args().action]()

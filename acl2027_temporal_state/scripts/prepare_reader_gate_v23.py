#!/usr/bin/env python3
"""Reconstruct 200 source-backed *typed control* candidates; never run a model.

Detailed derived records and references stay in the caller's external directory.
The public manifest contains hashes/selectors, not full source bodies or answers.
These controls cannot open the natural text/table reader gate.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from temporal_state.typed_reader_v15_1 import read_inline_xbrl


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bytes_digest(value):
    return hashlib.sha256(value).hexdigest()


def exact_decimal(operation, operands):
    """Exact finite-decimal arithmetic, including signed values and zeros."""
    if operation not in {"identity", "subtract"}:
        raise ValueError("unsupported arithmetic")
    if len(operands) != (1 if operation == "identity" else 2):
        raise ValueError("wrong operand count")
    if any(not isinstance(v, str) or len(v) > 10000 for v in operands):
        raise ValueError("operands must be bounded strings")
    try:
        values = [Decimal(v) for v in operands]
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if any(not v.is_finite() for v in values):
        raise ValueError("non-finite decimal")
    precision = max(v.adjusted() for v in values) - min(v.as_tuple().exponent for v in values) + 4
    if precision > 10000 or any(abs(v.adjusted()) > 10000 for v in values):
        raise ValueError("arithmetic outside bounded profile")
    with localcontext() as ctx:
        ctx.prec = max(precision, 28)
        result = values[0] if operation == "identity" else values[0] - values[1]
        if result == 0:
            return "0"
        return format(result, "f")


def eligible_singletons(document):
    """Exclude any full binding with an invalid/unresolved or competing value."""
    groups = defaultdict(list)
    for fact in document["facts"]:
        if fact.get("binding_status") == "reported_aspects_resolved" and fact.get("visibility") != "hidden_markup":
            groups[canonical(fact["reported_aspects"])].append(fact)
    admitted = []
    exclusions = Counter()
    for key, facts in sorted(groups.items()):
        if any(f.get("status") != "normalized" for f in facts):
            exclusions["non_normalized_occurrence"] += 1
            continue
        if len({Decimal(f["normalized_value"]) for f in facts}) != 1:
            exclusions["competing_values_same_binding"] += 1
            continue
        # Most local namespace/QName ambiguity is excluded by the existing parser;
        # no taxonomy equivalence/default or financial-scope assertion is added.
        fact = min(facts, key=lambda f: f["fact_ordinal"])
        try:
            exact_decimal("identity", [fact["normalized_value"]])
        except ValueError:
            exclusions["arithmetic_outside_profile"] += 1
            continue
        admitted.append(fact)
    return admitted, dict(exclusions)


def fact_key(fact):
    return digest([fact["source_sha256"], fact["reported_aspects"]])


def public_selector(fact):
    return {"source_sha256": fact["source_sha256"], "source_version": fact["source_version"],
            "fact_ordinal": fact["fact_ordinal"], "anchor": fact["anchor"],
            "evidence_anchors": fact["evidence_anchors"], "reported_scope_sha256": digest(fact["reported_aspects"])}


def scope(fact):
    return {"source_version": fact["source_version"], "source_sha256": fact["source_sha256"],
            **deepcopy(fact["reported_aspects"])}


def question_for(operation, targets):
    prefix = ("Use only the supplied reported-aspect records. Empty dimensions mean no explicit "
              "dimension member was reported; they do not assert a semantic total. ")
    if operation == "identity":
        return prefix + "What exact amount in base units is reported with this scope? " + canonical(scope(targets[0]))
    return (prefix + "What is the exact arithmetic difference in base units: the amount reported with "
            "scope A minus the amount reported with scope B? This asks for arithmetic, not a claim of "
            "economic comparability. Scope A: " + canonical(scope(targets[0])) + " Scope B: " + canonical(scope(targets[1])))


def render_request(item, protocol):
    """Whitelist serialization: no answer, family, condition, history, or file IDs."""
    evidence = [{"evidence_id": e["evidence_id"], **deepcopy(e["record"])} for e in item["evidence"]]
    user = canonical({"question": item["question"], "evidence": evidence})
    return {"messages": [{"role": "system", "content": protocol["system_prompt"]},
                         {"role": "user", "content": user}]}


def make_pair(targets, all_facts, history, protocol):
    operation = "identity" if len(targets) == 1 else "subtract"
    family = digest([protocol["data_design"]["selection_seed"], [fact_key(t) for t in targets], operation])[:24]
    target_keys = {fact_key(t) for t in targets}
    distractors = sorted((f for f in all_facts if fact_key(f) not in target_keys), key=lambda f: digest([family, fact_key(f)]))[:6]
    if len(distractors) < 5:
        raise ValueError("too few distinct distractors")
    question = question_for(operation, targets)
    answer = exact_decimal(operation, [t["normalized_value"] for t in targets])
    output = []
    for condition in ("complete", "designed_insufficient"):
        item_id = digest([family, condition, "opaque-item"])[:24]
        retained_targets = targets if condition == "complete" else targets[:-1]
        present = list(retained_targets) + distractors[:5 - len(retained_targets)]
        present.sort(key=lambda f: digest([item_id, fact_key(f)]))
        evidence, fact_to_eid = [], {}
        for i, f in enumerate(present, 1):
            evidence_id = f"E{i}"
            fact_to_eid[fact_key(f)] = evidence_id
            evidence.append({"evidence_id": evidence_id, "record": {**scope(f), "normalized_value": f["normalized_value"]},
                             "selector": public_selector(f)})
        operands = [fact_to_eid[fact_key(t)] for t in targets] if condition == "complete" else []
        reference = {"status": "answer" if condition == "complete" else "abstain",
                     "value": answer if condition == "complete" else None,
                     "unit": deepcopy(targets[0]["reported_aspects"]["unit"]) if condition == "complete" else None,
                     "operation": operation if condition == "complete" else None,
                     "operand_evidence_ids": operands, "evidence_ids": sorted(operands)}
        item = {"item_id": item_id, "family_id": family, "history_id": history,
                "evidence_condition": condition,
                "stratum": "reported_fact" if operation == "identity" else "same_filing_difference",
                "question": question, "evidence": evidence, "reference": reference,
                "target_selectors": [public_selector(t) for t in targets],
                "required_scopes": [scope(t) for t in targets],
                "review": {"status": "program_derived_pending_source_review",
                           "question_origin": "authored_template", "reference_origin": "parsed_reported_aspects_and_exact_decimal",
                           "human_validation": False,
                           "insufficiency_audit": "necessary_complete_amount_record_absent_from_rendered_records; no relations/other evidence admitted" if condition != "complete" else None,
                           "natural_source_insufficiency": False,
                           "natural_reader_gate_eligible": False}}
        item["request_sha256"] = digest(render_request(item, protocol))
        item["reference_sha256"] = digest(reference)
        output.append(item)
    return output


def select_families(by_file, protocol):
    """Predeclared deterministic round robin; no labels/model outputs consulted."""
    seed = protocol["data_design"]["selection_seed"]
    names = sorted(by_file, key=lambda n: digest([seed, n]))
    used = set()
    families = []
    queues = {}
    for name in names:
        queues[name] = sorted(by_file[name]["facts"], key=lambda f: digest([seed, "identity", fact_key(f)]))
    for i in range(50):
        name = names[i % len(names)]
        available = [f for f in queues[name] if fact_key(f) not in used]
        if not available:
            raise ValueError(f"not enough singleton controls in {name}; stop, do not substitute")
        chosen = available[0]
        used.add(fact_key(chosen))
        families.append((name, [chosen]))
    pair_queues = {}
    for name in names:
        groups = defaultdict(list)
        for f in by_file[name]["facts"]:
            a = {k: v for k, v in f["reported_aspects"].items() if k != "period"}
            groups[canonical(a)].append(f)
        pairs = []
        for values in groups.values():
            values.sort(key=lambda f: digest([seed, "period", fact_key(f)]))
            if len(values) >= 2:
                pairs.extend([values[j:j+2] for j in range(0, len(values)-1, 2)])
        pair_queues[name] = sorted(pairs, key=lambda p: digest([seed, "subtract", [fact_key(f) for f in p]]))
    for i in range(50):
        name = names[(i + 6) % len(names)]
        available = [p for p in pair_queues[name] if not any(fact_key(f) in used for f in p)]
        if not available:
            raise ValueError(f"not enough same-filing difference controls in {name}; stop, do not substitute")
        chosen = available[0]
        used.update(fact_key(f) for f in chosen)
        families.append((name, chosen))
    return families


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--external-output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--protocol", type=Path, default=ROOT / "data/reader_gate_v23/protocol.json")
    args = parser.parse_args()
    if args.external_output.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("detailed derived sources/references must remain outside repository")
    if args.manifest.exists() or args.external_output.exists():
        raise ValueError("refusing to overwrite a preparation; choose a new directory/version")
    protocol_raw = args.protocol.read_bytes()
    protocol = json.loads(protocol_raw)
    freeze = json.loads(args.protocol.with_name("protocol_freeze.json").read_text())
    if bytes_digest(protocol_raw) != freeze["protocol_sha256"]:
        raise ValueError("protocol no longer matches pre-candidate freeze")
    recovery_path = ROOT / "data/typed_reader_v15/source_recovery_protocol.json"
    recovery = json.loads(recovery_path.read_text())
    by_file, sources = {}, []
    for entry in recovery["files"]:
        source_path = args.source_dir / entry["external_filename"]
        raw = source_path.read_bytes()
        if bytes_digest(raw) != entry["expected_sha256"] or len(raw) != entry["expected_bytes"]:
            raise ValueError(f"source integrity failure: {source_path.name}")
        doc = read_inline_xbrl(raw, source_version=entry["external_filename"])
        facts, exclusions = eligible_singletons(doc)
        by_file[entry["external_filename"]] = {"facts": facts, "history_id": entry["issuer_id"]}
        sources.append({"source_version": entry["external_filename"], "source_sha256": entry["expected_sha256"],
                        "bytes": len(raw), "eligible_singleton_bindings": len(facts), "exclusions": exclusions})
    families = select_families(by_file, protocol)
    items = []
    for name, targets in families:
        items.extend(make_pair(targets, by_file[name]["facts"], by_file[name]["history_id"], protocol))
    items.sort(key=lambda item: digest([protocol["data_design"]["selection_seed"], item["item_id"], "request-order"]))
    if len({item["item_id"] for item in items}) != 200:
        raise ValueError("unexpected item count/collision")
    requests = [{"item_id": item["item_id"], "request_sha256": item["request_sha256"],
                 "request": render_request(item, protocol)} for item in items]
    manifest = {"schema_version": "reader_control_manifest_v23", "stage": "200_source_backed_control_candidates_prepared_no_predictions",
                "natural_reader_gate": "closed_not_tested_by_typed_controls", "protocol_sha256": bytes_digest(protocol_raw),
                "source_protocol_sha256": bytes_digest(recovery_path.read_bytes()),
                "generator_sha256": bytes_digest(Path(__file__).read_bytes()),
                "parser_sha256": bytes_digest((ROOT / "src/temporal_state/typed_reader_v15_1.py").read_bytes()),
                "items_content_sha256": digest(items), "requests_content_sha256": digest(requests),
                "count": len(items), "condition_counts": dict(Counter(i["evidence_condition"] for i in items)),
                "stratum_counts": dict(Counter(i["stratum"] for i in items)),
                "history_counts": dict(Counter(i["history_id"] for i in items)), "sources": sources,
                "reference_status": "program_derived_pending_independent_source_review",
                "question_status": "new_authored_templates_reported_aspects_only_not_natural_QA",
                "model_predictions": 0, "human_adjudications": 0,
                "items": [{k: deepcopy(i[k]) for k in ("item_id", "family_id", "history_id", "evidence_condition", "stratum", "target_selectors", "request_sha256", "reference_sha256")} for i in items]}
    write_new(args.external_output / "items.json", {"schema_version": "reader_control_items_v23", "items": items})
    write_new(args.external_output / "requests.json", requests)
    write_new(args.manifest, manifest)
    print(json.dumps({k: manifest[k] for k in ("stage", "count", "condition_counts", "history_counts", "natural_reader_gate")}, indent=2))


if __name__ == "__main__":
    main()

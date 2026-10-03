#!/usr/bin/env python3
"""Replay annotated atomic support withdrawal; no decoding, scoring, or QA.

The annotation is a working artifact containing exact quotes. The output is a
hash-bound projection and contains neither source text nor evidence quotes.
See docs/correction_gate_replay_schema_v10.txt for the strict input contract.
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from temporal_state.bounded import EvidenceSpan, TemporalClaim, claim_to_dict
from temporal_state.corrections import (
    CorrectionEvidence, CorrectionPolicy, SourceAuthority, SupportDependency,
    materialize_corrected_selection, validate_correction_policy,
)
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Source, validate_date


SCHEMA = "correction_gate_annotation_v0.10"
SCORE_DISCLAIMER = (
    "All unary, link, and null numeric fields are neutral 0.0 schema placeholders. "
    "All claims and specified correction links are selected explicitly; no scores "
    "are optimized, learned, or interpreted as natural predictions."
)
CORE_FILES = (
    "src/temporal_state/__init__.py", "src/temporal_state/bounded.py",
    "src/temporal_state/corrections.py", "src/temporal_state/coupled.py",
    "src/temporal_state/decoder.py", "src/temporal_state/models.py",
)


def digest(raw):
    return sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Nonfinite JSON number")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=constant)


def fields(value, required, optional=(), where="object"):
    require(isinstance(value, dict), f"{where} must be an object")
    require(set(required) <= set(value) <= set(required) | set(optional),
            f"Unexpected or missing fields in {where}")


def text(value, where, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())),
            f"{where} must be {'a string' if empty else 'nonempty text'}")
    return value


def array(value, where, nonempty=False):
    require(isinstance(value, list) and (not nonempty or bool(value)),
            f"{where} must be {'a nonempty' if nonempty else 'a'} JSON array")
    return value


def unique(items, where):
    require(len(items) == len(set(items)), f"Duplicate {where}")


def day(value, where):
    text(value, where)
    validate_date(value)
    return value


def evidence(row, source_text, where):
    fields(row, {"start", "end", "quote"}, where=where)
    require(type(row["start"]) is int and type(row["end"]) is int,
            f"{where} offsets must be integers, not booleans")
    text(row["quote"], f"{where} quote")
    require(0 <= row["start"] < row["end"] <= len(source_text),
            f"{where} offsets are outside the source")
    require(source_text[row["start"]:row["end"]] == row["quote"],
            f"{where} quote differs from the exact source slice")
    return EvidenceSpan(row["start"], row["end"], row["quote"])


def evidence_list(rows, source_text, where):
    return tuple(evidence(row, source_text, where)
                 for row in array(rows, where, nonempty=True))


def load_inputs(annotation_path, fulltext_root):
    raw = annotation_path.read_bytes()
    annotation = strict_json(raw)
    fields(annotation, {"schema_version", "history_id", "operational_cutoff",
           "sources", "claims", "authorities", "corrections", "dependencies",
           "unaffected_claim_ids"}, {"source_representation"}, "annotation")
    require(annotation["schema_version"] == SCHEMA, "Unsupported annotation schema")
    text(annotation["history_id"], "history_id")
    representation = text(annotation.get("source_representation",
        "supplied_text_unspecified_representation"), "source_representation")
    cutoff = day(annotation["operational_cutoff"], "operational_cutoff")
    root = fulltext_root.resolve(strict=True)
    require(root.is_dir(), "fulltext-root must be a directory")
    sources, manifests, source_paths = {}, [], []
    for row in array(annotation["sources"], "sources", nonempty=True):
        fields(row, {"source_id", "local_path", "sha256", "capture_date"},
               {"uri"}, "source")
        sid = text(row["source_id"], "source_id")
        require(sid not in sources, "Duplicate source ID")
        local = Path(text(row["local_path"], "local_path"))
        require(not local.is_absolute() and ".." not in local.parts,
                "Source local_path must be relative beneath fulltext-root")
        path = (root / local).resolve(strict=True)
        require(path.is_relative_to(root) and path.is_file(),
                "Source path must resolve to a file beneath fulltext-root")
        expected = text(row["sha256"], "source sha256")
        require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
                "Source sha256 must be a lowercase SHA-256 digest")
        source_bytes = path.read_bytes()
        require(digest(source_bytes) == expected, "Source file SHA-256 mismatch")
        source_text = source_bytes.decode("utf-8")
        require(bool(source_text.strip()), "Supplied source text must be nonempty")
        capture_day = day(row["capture_date"], "capture_date")
        uri = text(row.get("uri", ""), "source uri", empty=True)
        sources[sid] = Source(sid, source_text, capture_day, uri=uri,
            availability_basis="actual_capture_day_operational_only_not_historical_availability",
            provenance="hash_bound_supplied_text; representation=" + representation
                       + "; capture_date_declared_by_annotation")
        manifests.append({"source_id": sid, "local_path": local.as_posix(),
            "sha256": expected, "capture_date": capture_day, "uri": uri,
            "bytes": len(source_bytes), "characters": len(source_text)})
        source_paths.append(path)
    require(cutoff == max(source.available_at for source in sources.values()),
            "Operational cutoff must equal the latest actual source capture date")
    return annotation, raw, sources, manifests, source_paths


def build_problem(annotation, sources):
    context = tuple(sources)
    claims, mentions, by_id = {}, [], {}
    for row in array(annotation["claims"], "claims", nonempty=True):
        fields(row, {"claim_id", "source_id", "entity", "relation", "value",
               "scope", "modality", "evidence_spans"}, {"reported_at"}, "claim")
        cid = text(row["claim_id"], "claim_id")
        require(cid not in by_id, "Duplicate claim ID")
        sid = text(row["source_id"], "claim source_id")
        require(sid in sources, "Claim refers to an unknown source")
        for name in ("entity", "relation", "value", "modality"):
            text(row[name], f"claim {name}")
        text(row["scope"], "claim scope", empty=True)
        reported_at = row.get("reported_at")
        if reported_at is not None:
            day(reported_at, "reported_at")
        spans = evidence_list(row["evidence_spans"], sources[sid].text, "claim evidence")
        claim = TemporalClaim(cid, sid, row["entity"], row["relation"], row["value"],
            scope=row["scope"], modality=row["modality"], reported_at=reported_at,
            evidence_spans=spans, context_source_ids=context, raw_assertion_id=cid,
            derivation_note="Supplied source annotation; explicitly selected; no event bounds or observations assigned.")
        mid = "mention:" + cid
        claims[mid, "selected"] = claim
        by_id[cid] = (mid, claim)
        mentions.append(Mention(mid, sid, spans[0].start, spans[0].end,
            (Reading("selected", claim.key, claim.value, 0.0, context_source_ids=context),
             Reading("unresolved", None, None, 0.0, context_source_ids=context)),
            null_score=0.0, context_source_ids=context))

    authorities = []
    for row in array(annotation["authorities"], "authorities"):
        fields(row, {"source_id", "authority_id", "evidence_spans"}, where="authority")
        sid = text(row["source_id"], "authority source_id")
        require(sid in sources, "Authority refers to an unknown source")
        authorities.append(SourceAuthority(sid, text(row["authority_id"], "authority_id"),
            evidence_list(row["evidence_spans"], sources[sid].text, "authority evidence")))

    links, certificates, replacement_ids, correction_pairs = [], [], [], []
    for index, row in enumerate(array(annotation["corrections"], "corrections", nonempty=True)):
        fields(row, {"replacement_claim_id", "target_claim_id", "reference_span"},
               where="correction")
        replacement = text(row["replacement_claim_id"], "replacement_claim_id")
        target = text(row["target_claim_id"], "target_claim_id")
        require(replacement in by_id and target in by_id, "Correction refers to an unknown claim")
        require(replacement != target, "A correction cannot target itself")
        require(replacement not in replacement_ids,
                "Core supports one selected correction per replacement claim; no correction may be dropped")
        replacement_ids.append(replacement)
        mid, claim = by_id[replacement]
        target_mid, target_claim = by_id[target]
        span = evidence(row["reference_span"], sources[claim.source_id].text, "correction reference")
        lid = f"correction:{index:04d}"
        links.append(Link(lid, mid, "selected", target_mid, "selected", "CORRECTS", 0.0,
            context_source_ids=context, reference_span=(span.start, span.end)))
        certificates.append(CorrectionEvidence(lid, span))
        correction_pairs.append({"link_id": lid, "replacement_claim_id": replacement,
            "target_claim_id": target, "replacement_modality": claim.modality,
            "target_modality": target_claim.modality})

    dependencies = []
    for row in array(annotation["dependencies"], "dependencies"):
        fields(row, {"dependent_claim_id", "prerequisite_claim_id", "evidence_spans"},
               where="dependency")
        child = text(row["dependent_claim_id"], "dependent_claim_id")
        parent = text(row["prerequisite_claim_id"], "prerequisite_claim_id")
        require(child in by_id and parent in by_id, "Dependency refers to an unknown claim")
        source = sources[by_id[child][1].source_id]
        dependencies.append(SupportDependency(child, parent,
            evidence_list(row["evidence_spans"], source.text, "dependency evidence")))

    unaffected = array(annotation["unaffected_claim_ids"], "unaffected_claim_ids", nonempty=True)
    for cid in unaffected:
        text(cid, "unaffected claim ID")
        require(cid in by_id, "Unaffected control refers to an unknown claim")
    unique(unaffected, "unaffected claim ID")
    problem = Problem(annotation["operational_cutoff"], tuple(sources.values()),
        tuple(mentions), tuple(links), SCORE_DISCLAIMER, context_source_ids=context)
    policy = CorrectionPolicy(source_authorities=tuple(authorities),
        correction_evidence=tuple(certificates), support_dependencies=tuple(dependencies))
    validate_correction_policy(problem, claims, policy)
    return problem, claims, policy, correction_pairs


def projected_span(span):
    return {"start": span["start"], "end": span["end"],
            "quote_sha256": digest(span["quote"].encode("utf-8")),
            "quote_characters": len(span["quote"])}


def projected_claim(claim):
    return {"claim_id": claim["claim_id"], "source_id": claim["source_id"],
        "full_claim_record_sha256": digest(canonical(claim)),
        "polarity": claim["polarity"], "modality": claim["modality"],
        "reported_at": claim["reported_at"], "start": claim["start"],
        "end": claim["end"], "state_observed_at": claim["state_observed_at"],
        "evidence_spans": [projected_span(span) for span in claim["evidence_spans"]],
        "context_source_ids": claim["context_source_ids"]}


def projected_ledger(ledger):
    # Explicit allowlist: never serialize arbitrary diagnostics or claim text.
    keys = ("policy_version", "selected_reading_ids", "selected_link_ids",
        "active_claim_ids", "directly_withdrawn_claim_ids", "inactive_claim_ids",
        "inactive_reasons", "operative_notices", "active_temporal_link_ids",
        "inactive_temporal_link_ids", "control_source_ids",
        "inactive_claim_control_source_ids", "support_dependency_semantics",
        "restatement_dependency_semantics", "reinstatement_policy",
        "certificate_status", "time_policy")
    result = {key: ledger[key] for key in keys}
    result["selected_claims"] = [projected_claim(claim) for claim in ledger["selected_claims"]]
    result["full_ledger_sha256"] = digest(canonical(ledger))
    result["projection"] = "Claim text omitted; exact source evidence represented by offsets and quote hashes."
    return result


def replay(annotation_path, fulltext_root):
    annotation, raw, sources, source_manifest, source_paths = load_inputs(annotation_path, fulltext_root)
    problem, claims, full_policy, correction_pairs = build_problem(annotation, sources)
    selected = {mention.mention_id: "selected" for mention in problem.mentions}
    null_links = {mention.mention_id: None for mention in problem.mentions}
    direct_links = dict(null_links)
    direct_links.update({link.mention_id: link.link_id for link in problem.links})
    policies = {
        "no_withdrawal": CorrectionPolicy(source_authorities=full_policy.source_authorities),
        "direct_withdrawal": CorrectionPolicy(source_authorities=full_policy.source_authorities,
                                             correction_evidence=full_policy.correction_evidence),
        "dependency_withdrawal": full_policy,
    }
    original = {claim.claim_id: claim_to_dict(claim) for claim in claims.values()}
    records = []
    for name, policy in policies.items():
        chosen = null_links if name == "no_withdrawal" else direct_links
        validate_correction_policy(problem, claims, policy)
        memory = materialize_corrected_selection(problem, selected, chosen, claims, policy)
        ledger = memory.diagnostics["correction_ledger"]
        retained_original = {claim["claim_id"]: claim for claim in ledger["selected_claims"]}
        require(retained_original == original, "Core changed an original selected claim record")
        unknown = all(claim.start.lower is None and claim.start.upper is None
                      and claim.end.lower is None and claim.end.upper is None
                      and not claim.state_observed_at for claim in memory.claims)
        require(unknown, "Core did not preserve unknown endpoints and empty observations")
        active = set(ledger["active_claim_ids"])
        controls = {cid: cid in active for cid in annotation["unaffected_claim_ids"]}
        require(all(controls.values()), "A declared unaffected retention control was withdrawn")
        records.append({"policy": name, "policy_sha256": digest(canonical(asdict(policy))),
            "active_claim_ids": ledger["active_claim_ids"],
            "unknown_endpoints_preserved": unknown,
            "original_selected_records_preserved": True,
            "unaffected_claims_retained": controls,
            "ledger": projected_ledger(ledger)})
    by_name = {record["policy"]: record for record in records}
    direct_inactive = set(by_name["direct_withdrawal"]["ledger"]["inactive_claim_ids"])
    dependent_inactive = set(by_name["dependency_withdrawal"]["ledger"]["inactive_claim_ids"])
    require(direct_inactive <= dependent_inactive, "Dependency policy resurrected direct targets")
    core_hashes = {path: digest((ROOT / path).read_bytes()) for path in CORE_FILES}
    output = {
        "schema_version": "correction_gate_fixed_selection_replay_v0.10",
        "status": "completed_annotation_conditioned_policy_materialization",
        "history_id": annotation["history_id"],
        "annotation_sha256": digest(raw),
        "script_sha256": digest(Path(__file__).read_bytes()),
        "schema_document_sha256": digest((ROOT / "docs/correction_gate_replay_schema_v10.txt").read_bytes()),
        "core_sha256": core_hashes,
        "source_bindings": source_manifest,
        "source_delivery_order": list(sources),
        "source_representation": annotation.get("source_representation",
            "supplied_text_unspecified_representation"),
        "source_scope_limit": "Replay is restricted to the supplied hash-bound text representation; it does not certify full-document access, a complete version, or paired historical prefixes.",
        "operational_cutoff": problem.cutoff,
        "clock_policy": "Source available_at is the declared actual capture date; cutoff is max capture date. Neither is an event boundary or certified historical availability.",
        "capture_dates_independently_verified": False,
        "selection_policy": "All supplied claims selected in every arm; all supplied direct CORRECTS links selected in both withdrawal arms, including directly corrected downstream assertions.",
        "neutral_score_disclaimer": SCORE_DISCLAIMER,
        "correction_pairs": correction_pairs,
        "dependency_pairs": [{"dependent_claim_id": row.dependent_claim_id,
                              "prerequisite_claim_id": row.prerequisite_claim_id}
                             for row in full_policy.support_dependencies],
        "counts": {"sources": len(sources), "selected_claims": len(claims),
                   "direct_corrections": len(problem.links),
                   "essential_dependencies": len(full_policy.support_dependencies),
                   "unaffected_controls": len(annotation["unaffected_claim_ids"])},
        "policies": records,
        "dependency_additional_inactive_claim_ids": sorted(dependent_inactive - direct_inactive),
        "decoder_run": False, "qa_predictions": 0, "model_calls": 0,
        "model_scores_created": False, "natural_accuracy_claim": False,
        "semantic_entailment_certified": False,
        "method_advantage_claim": False,
        "interpretation": "A conditional annotation replay. Inactivity removes declared support; it does not establish falsity, compute replacement values, or predict QA answers. Empty extra withdrawal is reported without suppressing direct corrections.",
        "full_source_text_in_output": False, "evidence_quotes_in_output": False,
    }
    return output, source_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--fulltext-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output, source_paths = replay(args.annotation, args.fulltext_root)
        destination = args.output.resolve()
        protected = {args.annotation.resolve(), *source_paths,
                     Path(__file__).resolve(),
                     (ROOT / "docs/correction_gate_replay_schema_v10.txt").resolve(),
                     *((ROOT / path).resolve() for path in CORE_FILES)}
        require(destination not in protected, "Output cannot overwrite a replay input or implementation file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent,
                                         prefix=destination.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        print(json.dumps({"status": output["status"], "history_id": output["history_id"],
            "policies": len(output["policies"]), "qa_predictions": 0,
            "dependency_additional_inactive_claim_ids": output["dependency_additional_inactive_claim_ids"]}))
    except (ValueError, OSError, UnicodeError, KeyError, TypeError) as exc:
        parser.exit(1, f"Replay validation failed: {exc}\n")


if __name__ == "__main__":
    main()

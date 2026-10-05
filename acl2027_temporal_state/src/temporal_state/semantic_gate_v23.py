"""Version 23 natural-question pilot contracts and fail-closed authorization.

This validates an externally supplied audit, not semantic completeness, human
identity, or entailment. No LLM is called; no reference labels enter model input.
All equality classes use the frozen structured normalization, not numeric value
alone. See docs/semantic_gate_v23.txt for the trust boundary.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

SCHEMA = "semantic_pilot_v23"
OBLIGATIONS = (
    "source_inventory", "question_scope", "source_regions_and_continuations",
    "cross_source_identity", "material_conflicts", "answer_class_search",
)
REQUIRED_ASPECTS = (
    "concept", "entity", "period", "unit", "dimensions", "source_identity",
    "comparison_relation",
)
PILOT_CRITERIA = {
    "version": "proposal_development_gate_v23",
    "scheduled_natural_items": 200,
    "qualified_independent_reviews": 200,
    "complete_scoped_reference_inventories": 200,
    "minimum_nonempty_reference_items": 100,
    "minimum_micro_class_recall": 0.95,
    "minimum_nonempty_item_full_class_coverage": 0.95,
    "maximum_false_candidate_eliminations": 0,
    "every_refutation_reviewed": True,
    "authorizes_automatic_pipeline_comparison": False,
}


def digest(value: Any) -> str:
    """SHA256 of canonical UTF-8 JSON; strings are JSON encoded too."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _keys(value, required, name):
    _require(type(value) is dict and set(value) == set(required), f"{name}: unexpected or missing fields")


def _text(value, name):
    _require(type(value) is str and bool(value.strip()), f"{name}: nonempty string required")


def _hash(value, name):
    _require(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
             f"{name}: lowercase SHA256 required")


def _unique(records, key, name):
    _require(type(records) is list, f"{name}: list required")
    result = {}
    for record in records:
        _require(type(record) is dict and key in record, f"{name}: invalid record")
        _text(record[key], name)
        _require(record[key] not in result, f"{name}: duplicate {key}")
        result[record[key]] = record
    return result


def validate_input(item: dict) -> None:
    """Input is a strict whitelist: references, parsed facts and labels forbidden."""
    _keys(item, ("schema", "item_id", "question", "answer_slots", "normalization_spec", "normalization_sha256",
                 "corpus_policy", "observed_evidence"), "input")
    _require(item["schema"] == SCHEMA, "wrong schema")
    for name in ("item_id", "question"):
        _text(item[name], name)
    _hash(item["normalization_sha256"], "normalization")
    _text(item["normalization_spec"], "normalization specification")
    _require(item["normalization_sha256"] == text_digest(item["normalization_spec"]),
             "normalization specification digest mismatch")
    slots = item["answer_slots"]
    _require(type(slots) is list and slots and all(type(x) is str and x.strip() for x in slots)
             and len(set(slots)) == len(slots), "distinct requested answer slots required")
    policy = item["corpus_policy"]
    _keys(policy, ("policy_id", "sources", "included_regions", "excluded_regions",
                   "cutoff", "scope_definition"), "corpus policy")
    for name in ("policy_id", "cutoff", "scope_definition"):
        _text(policy[name], name)
    sources = _unique(policy["sources"], "source_id", "sources")
    _require(bool(sources), "nonempty declared source universe required")
    for source in sources.values():
        _keys(source, ("source_id", "source_sha256", "immutable_reference"), "source")
        _hash(source["source_sha256"], "source hash")
        _text(source["immutable_reference"], "immutable source reference")
    for name in ("included_regions", "excluded_regions"):
        _require(type(policy[name]) is list, f"{name}: list required")
        for region in policy[name]:
            _keys(region, ("source_id", "locator", "rationale"), "region")
            _require(region["source_id"] in sources, "region source outside universe")
            _text(region["locator"], "region locator")
            _text(region["rationale"], "region rationale")
    _require(bool(policy["included_regions"]), "included source regions required")
    evidence = _unique(item["observed_evidence"], "evidence_id", "evidence")
    for record in evidence.values():
        _keys(record, ("evidence_id", "source_id", "source_sha256", "locator", "text", "text_sha256"), "evidence")
        _require(record["source_id"] in sources, "evidence outside declared universe")
        _require(record["source_sha256"] == sources[record["source_id"]]["source_sha256"], "evidence source hash mismatch")
        for name in ("locator", "text"):
            _text(record[name], name)
        _require(record["text_sha256"] == text_digest(record["text"]), "observed text hash mismatch")


def model_input(item: dict) -> dict:
    """Never include IDs linking private references or a closed parsed-fact table."""
    validate_input(item)
    return {key: item[key] for key in ("question", "answer_slots", "normalization_spec", "normalization_sha256",
                                      "corpus_policy", "observed_evidence")}


def input_hash(item: dict) -> str:
    return digest(model_input(item))


def class_key(candidate: dict) -> str:
    return digest(sorted(({"slot": claim["slot"], "value": claim["value"], "aspects": claim["aspects"]}
                          for claim in candidate["claims"]), key=lambda c: c["slot"]))


def validate_proposals(item: dict, proposal: dict, proposer_config_sha256: str) -> dict:
    validate_input(item)
    _hash(proposer_config_sha256, "proposer configuration")
    _keys(proposal, ("input_sha256", "config_sha256", "candidates"), "proposal")
    _require(proposal["input_sha256"] == input_hash(item), "proposal bound to different model input")
    _require(proposal["config_sha256"] == proposer_config_sha256, "unfrozen proposer configuration")
    candidates = _unique(proposal["candidates"], "candidate_id", "candidates")
    evidence = {e["evidence_id"] for e in item["observed_evidence"]}
    sources = {s["source_id"] for s in item["corpus_policy"]["sources"]}
    for candidate in candidates.values():
        _keys(candidate, ("candidate_id", "claims"), "candidate")
        claims = _unique(candidate["claims"], "claim_id", "claims")
        _require(len(claims) == len(item["answer_slots"]), "incomplete joint answer")
        _require({c.get("slot") for c in claims.values()} == set(item["answer_slots"]), "requested answer slots missing")
        for claim in claims.values():
            _keys(claim, ("claim_id", "slot", "value", "aspects", "citations"), "claim")
            _text(claim["value"], "normalized value")
            _keys(claim["aspects"], REQUIRED_ASPECTS, "complete claim aspects")
            for aspect, value in claim["aspects"].items():
                _require(value is not None and value != "", f"unbound {aspect}")
                if aspect == "dimensions":
                    _require(type(value) is dict, "dimensions require an explicit map (possibly empty)")
                else:
                    _require((type(value) is str and bool(value.strip())) or
                             (type(value) is dict and bool(value)), f"empty or malformed {aspect}")
                digest(value)  # Reject NaN and non-JSON values.
            _require(type(claim["aspects"]["source_identity"]) is str and
                     claim["aspects"]["source_identity"] in sources, "claim source outside declared inventory")
            citations = claim["citations"]
            _require(type(citations) is list and citations and len(set(citations)) == len(citations)
                     and set(citations) <= evidence, "unobserved or absent proposal citations")
    return candidates


def verifier_input(item: dict, proposal: dict, proposer_config_sha256: str) -> dict:
    validate_proposals(item, proposal, proposer_config_sha256)
    return {"source_input": model_input(item), "proposal": proposal}


def validate_judgments(item: dict, proposal: dict, verification: dict, configs: dict) -> dict:
    candidates = validate_proposals(item, proposal, configs["proposer"])
    _keys(verification, ("input_sha256", "config_sha256", "judgments"), "verification")
    _require(verification["input_sha256"] == digest(verifier_input(item, proposal, configs["proposer"])), "verifier input mismatch")
    _require(verification["config_sha256"] == configs["verifier"], "unfrozen verifier configuration")
    judgments = _unique(verification["judgments"], "judgment_id", "judgments")
    evidence = {e["evidence_id"] for e in item["observed_evidence"]}
    for judgment in judgments.values():
        _keys(judgment, ("judgment_id", "candidate_id", "claim_id", "verdict", "bindings", "reason"), "judgment")
        _require(judgment["candidate_id"] in candidates, "unknown candidate judgment")
        claims = {c["claim_id"]: c for c in candidates[judgment["candidate_id"]]["claims"]}
        _require(judgment["claim_id"] in claims, "unknown claim judgment")
        _require(judgment["verdict"] in ("support", "refute", "unresolved"), "invalid verdict")
        _text(judgment["reason"], "judgment reason")
        bindings = judgment["bindings"]
        _require(type(bindings) is dict, "premise bindings required")
        all_aspects = {"value", *REQUIRED_ASPECTS}
        _require(set(bindings) <= all_aspects, "unknown premise aspect")
        if judgment["verdict"] == "support":
            _require(set(bindings) == all_aspects, "support omits a binding premise")
        if judgment["verdict"] == "refute":
            _require(bool(bindings), "refutation requires observed premise")
        for aspect, ids in bindings.items():
            _require(type(ids) is list and ids and len(set(ids)) == len(ids) and set(ids) <= evidence,
                     "judgment uses missing or unacquired premise")
    return judgments


def coverage_decision(item: dict, proposal: dict, audit: dict | None, configs: dict) -> tuple[bool, str]:
    """Audit inputs are external records, never candidate scores or a bool."""
    validate_proposals(item, proposal, configs["proposer"])
    if audit is None:
        return False, "external_audit_missing"
    _keys(audit, ("input_sha256", "proposal_sha256", "corpus_policy_sha256", "protocol_sha256",
                  "reviewer", "status", "source_reviews", "obligations", "unresolved_classes"), "coverage audit")
    for field, expected in (("input_sha256", input_hash(item)), ("proposal_sha256", digest(proposal)),
                            ("corpus_policy_sha256", digest(item["corpus_policy"])),
                            ("protocol_sha256", configs["coverage_protocol"])):
        _require(audit[field] == expected, f"coverage {field} mismatch")
    reviewer = audit["reviewer"]
    _keys(reviewer, ("identity", "kind", "independent_of_proposal", "independent_of_reference"), "coverage reviewer")
    _text(reviewer["identity"], "reviewer identity")
    _require(reviewer["kind"] in ("human", "model", "program"), "unknown reviewer provenance")
    for field in ("independent_of_proposal", "independent_of_reference"):
        _require(type(reviewer[field]) is bool, "reviewer independence must be explicit")
    _require(audit["status"] in ("complete", "unresolved", "incomplete"), "invalid audit status")
    _require(type(audit["unresolved_classes"]) is list, "unresolved classes must be recorded")
    sources = {s["source_id"]: s for s in item["corpus_policy"]["sources"]}
    reviews = _unique(audit["source_reviews"], "source_id", "source reviews")
    for source_id, review in reviews.items():
        _keys(review, ("source_id", "source_sha256", "inspected_regions", "review_text", "review_sha256"), "source review")
        _require(source_id in sources and review["source_sha256"] == sources[source_id]["source_sha256"], "review source mismatch")
        _require(type(review["inspected_regions"]) is list and review["inspected_regions"], "inspected regions missing")
        for region in review["inspected_regions"]:
            _text(region, "inspected region")
        _text(review["review_text"], "source review record")
        _require(review["review_sha256"] == text_digest(review["review_text"]), "source review digest mismatch")
    obligations = _unique(audit["obligations"], "obligation", "coverage obligations")
    _require(set(obligations) == set(OBLIGATIONS), "coverage obligations omitted")
    for name, obligation in obligations.items():
        _keys(obligation, ("obligation", "status", "source_ids", "finding"), "obligation")
        _require(obligation["status"] in ("satisfied", "not_applicable", "unresolved"), "invalid obligation status")
        _require(type(obligation["source_ids"]) is list and set(obligation["source_ids"]) <= set(reviews), "obligation cites unreviewed source")
        _text(obligation["finding"], "obligation finding")
        if name != "cross_source_identity":
            _require(obligation["status"] != "not_applicable", "mandatory obligation cannot be waived")
        elif obligation["status"] == "not_applicable":
            _require(len(sources) == 1, "multi-source identity obligation cannot be waived")
        if obligation["status"] == "satisfied":
            _require(bool(obligation["source_ids"]), "satisfied obligation lacks source audit")
    if reviewer["kind"] != "human":
        return False, "nonhuman_audit_diagnostic_only"
    if not reviewer["independent_of_proposal"] or not reviewer["independent_of_reference"]:
        return False, "independent_audit_required"
    if set(reviews) != set(sources):
        return False, "declared_source_not_reviewed"
    included = {(r["source_id"], r["locator"]) for r in item["corpus_policy"]["included_regions"]}
    inspected = {(sid, region) for sid, r in reviews.items() for region in r["inspected_regions"]}
    if not included <= inspected:
        return False, "declared_region_not_reviewed"
    if audit["status"] != "complete" or audit["unresolved_classes"] or any(o["status"] == "unresolved" for o in obligations.values()):
        return False, "coverage_unresolved"
    return True, "externally_attested_scoped_coverage"


def authorize(item: dict, proposal: dict, verification: dict, audit: dict | None,
              configs: dict, winner_id: str) -> dict:
    """Mechanical certificate over already observed text; no hidden future labels."""
    candidates = validate_proposals(item, proposal, configs["proposer"])
    judgments = validate_judgments(item, proposal, verification, configs)
    _require(winner_id in candidates, "winner absent")
    signs = {}
    proposition_signs = {}
    for judgment in judgments.values():
        signs.setdefault((judgment["candidate_id"], judgment["claim_id"]), set()).add(judgment["verdict"])
        claim = next(c for c in candidates[judgment["candidate_id"]]["claims"]
                     if c["claim_id"] == judgment["claim_id"])
        proposition = digest({"value": claim["value"], "aspects": claim["aspects"]})
        proposition_signs.setdefault(proposition, set()).add(judgment["verdict"])
    if any({"support", "refute"} <= values for values in proposition_signs.values()):
        return {"authorized": False, "reason": "observed_conflict"}
    winner = candidates[winner_id]
    if any("support" not in signs.get((winner_id, c["claim_id"]), set()) for c in winner["claims"]):
        return {"authorized": False, "reason": "winner_not_jointly_supported"}
    for candidate_id, candidate in candidates.items():
        if class_key(candidate) != class_key(winner) and not any(
                "refute" in signs.get((candidate_id, c["claim_id"]), set()) for c in candidate["claims"]):
            return {"authorized": False, "reason": "unrefuted_rival"}
    cleared, reason = coverage_decision(item, proposal, audit, configs)
    return {"authorized": cleared, "reason": reason}


def proposal_development_gate(counts: dict, micro_recall: float | None,
                              full_class_coverage: float | None, population: str) -> dict:
    """Prospective engineering rule; no statistical or verifier-risk guarantee."""
    failures = []
    if population != "natural_source_questions":
        failures.append("natural_question_population_not_established")
    for key, required in (("scheduled", 200), ("qualified_review_items", 200),
                          ("class_recall_eligible_items", 200)):
        if counts.get(key, 0) != required:
            failures.append(f"{key}_must_equal_{required}")
    if counts.get("nonempty_reference_items", 0) < 100:
        failures.append("fewer_than_100_nonempty_reference_items")
    if micro_recall is None or micro_recall < 0.95:
        failures.append("micro_class_recall_below_0.95_or_unmeasured")
    if full_class_coverage is None or full_class_coverage < 0.95:
        failures.append("full_class_coverage_below_0.95_or_unmeasured")
    if counts.get("questions_with_false_elimination", 0) != 0:
        failures.append("reviewed_false_candidate_elimination")
    return {"passed": not failures, "scope": "proposal_development_readiness_only",
            "criteria_sha256": digest(PILOT_CRITERIA), "failures": failures,
            "verification_judgment_error_status": (
                "verification_judgment_error_insufficient_opportunities"
                if counts.get("reviewed_refutations", 0) == 0 else "descriptive_reviewed_opportunities_only"),
            "automatic_pipeline_comparison_authorized": False,
            "automatic_coverage_status": "not_implemented_or_validated"}


def analyze_pilot(bundle: dict) -> dict:
    """Compute descriptive reviewed metrics; never infer labels from certificates.

    Empty/missing/unreviewed references yield null recall/risk, not perfect scores.
    No hypothesis tests or confidence intervals: histories and primary estimand
    belong to the separately frozen confirmatory protocol.
    """
    _keys(bundle, ("schema", "manifest", "items", "runs", "reviews"), "pilot bundle")
    _require(bundle["schema"] == SCHEMA, "wrong pilot schema")
    manifest = bundle["manifest"]
    _keys(manifest, ("status", "population", "pilot_criteria_sha256", "configs", "item_input_hashes", "reference_hashes", "review_protocol_sha256"), "manifest")
    _require(manifest["status"] == "frozen", "pilot manifest not frozen")
    _require(manifest["population"] in ("natural_source_questions", "typed_controls", "authored_contract_controls"), "undeclared population")
    _require(manifest["pilot_criteria_sha256"] == digest(PILOT_CRITERIA), "engineering gate criteria changed")
    configs = manifest["configs"]
    _keys(configs, ("proposer", "verifier", "coverage_protocol"), "configs")
    for value in configs.values():
        _hash(value, "frozen configuration hash")
    _hash(manifest["review_protocol_sha256"], "review protocol hash")
    items = _unique(bundle["items"], "item_id", "items")
    runs = _unique(bundle["runs"], "item_id", "runs")
    reviews = _unique(bundle["reviews"], "item_id", "reviews")
    _require(set(items) == set(manifest["item_input_hashes"]), "scheduled item roster changed")
    _require(set(items) == set(manifest["reference_hashes"]), "reference roster changed")
    for value in manifest["reference_hashes"].values():
        if value is not None:
            _hash(value, "frozen private reference hash")
    _require(set(runs) <= set(items) and set(reviews) <= set(runs), "run or review outside schedule")
    counts = Counter(scheduled=len(items), missing_runs=len(set(items) - set(runs)))
    history = {}
    for item_id, item in items.items():
        _require(input_hash(item) == manifest["item_input_hashes"][item_id], "frozen item changed")
        if item_id not in runs:
            continue
        run = runs[item_id]
        _keys(run, ("item_id", "proposal", "verification", "coverage_audit", "winner_id", "raw_output"), "run")
        _require(type(run["raw_output"]) is str, "raw output must be preserved")
        candidates = validate_proposals(item, run["proposal"], configs["proposer"])
        judgments = validate_judgments(item, run["proposal"], run["verification"], configs)
        if run["winner_id"] is not None:
            decision = authorize(item, run["proposal"], run["verification"], run["coverage_audit"], configs, run["winner_id"])
            counts["mechanically_authorized"] += decision["authorized"]
        else:
            coverage_decision(item, run["proposal"], run["coverage_audit"], configs)
        if item_id not in reviews:
            counts["missing_reviews"] += 1
            continue
        review = reviews[item_id]
        _keys(review, ("item_id", "input_sha256", "run_sha256", "protocol_sha256", "history_id", "reviewer",
                       "reference_record", "reference_complete", "reference_class_ids", "candidate_matches", "refutation_labels",
                       "exposed_answer", "joint_answer_correct", "review_text"), "independent review")
        _require(review["input_sha256"] == input_hash(item) and review["run_sha256"] == digest(run), "review binding mismatch")
        _require(review["protocol_sha256"] == manifest["review_protocol_sha256"], "review protocol changed")
        _text(review["review_text"], "review record")
        _text(review["history_id"], "history ID")
        _keys(review["reviewer"], ("identity", "kind", "independent_of_models", "reference_written_before_prediction"), "reference reviewer")
        _text(review["reviewer"]["identity"], "reference reviewer identity")
        _require(review["reviewer"]["kind"] in ("human", "model", "program"), "unknown reference provenance")
        for name in ("independent_of_models", "reference_written_before_prediction"):
            _require(type(review["reviewer"][name]) is bool, "reference independence must be explicit")
        for name in ("reference_complete", "exposed_answer"):
            _require(type(review[name]) is bool, f"{name} must be boolean")
        _require(review["joint_answer_correct"] is None or type(review["joint_answer_correct"]) is bool,
                 "invalid answer correctness")
        if review["reviewer"]["kind"] != "human" or not review["reviewer"]["independent_of_models"] or not review["reviewer"]["reference_written_before_prediction"]:
            counts["nonqualifying_reviews"] += 1
            continue
        reference = review["reference_record"]
        _keys(reference, ("item_id", "input_sha256", "class_ids", "scope_status", "source_reviews", "class_inventory_review"), "private preprediction reference")
        _require(manifest["reference_hashes"][item_id] is not None and
                 digest(reference) == manifest["reference_hashes"][item_id], "reference was not frozen before predictions")
        _require(reference["item_id"] == item_id and reference["input_sha256"] == input_hash(item), "reference input mismatch")
        _require(reference["scope_status"] in ("complete", "unresolved"), "invalid reference scope status")
        _require(review["reference_complete"] == (reference["scope_status"] == "complete") and
                 review["reference_class_ids"] == reference["class_ids"], "postprediction reference changed")
        _text(reference["class_inventory_review"], "preprediction class inventory and complete scoped answers")
        source_reviews = _unique(reference["source_reviews"], "source_id", "reference source reviews")
        sources = {s["source_id"]: s for s in item["corpus_policy"]["sources"]}
        for sid, source_review in source_reviews.items():
            _keys(source_review, ("source_id", "source_sha256", "review_text", "review_sha256"), "reference source review")
            _require(sid in sources and source_review["source_sha256"] == sources[sid]["source_sha256"], "reference source identity mismatch")
            _text(source_review["review_text"], "reference source review text")
            _require(source_review["review_sha256"] == text_digest(source_review["review_text"]), "reference review hash mismatch")
        if reference["scope_status"] == "complete":
            _require(set(source_reviews) == set(sources), "complete reference omits declared source")
        counts["qualified_review_items"] += 1
        hc = history.setdefault(review["history_id"], Counter())
        hc["reviewed"] += 1
        if review["exposed_answer"]:
            _require(type(review["joint_answer_correct"]) is bool, "exposed answer requires reviewed correctness")
            counts["reviewed_emissions"] += 1
            hc["emissions"] += 1
            counts["reviewed_bad_emissions"] += not review["joint_answer_correct"]
            hc["bad_emissions"] += not review["joint_answer_correct"]
        else:
            _require(review["joint_answer_correct"] is None, "refusal correctness is not answer correctness")
        ref_ids = review["reference_class_ids"]
        _require(type(ref_ids) is list and len(set(ref_ids)) == len(ref_ids) and all(type(r) is str and r for r in ref_ids), "invalid reference classes")
        matches = _unique(review["candidate_matches"], "candidate_id", "reviewed candidate matches")
        _require(set(matches) == set(candidates), "every proposed candidate needs independent validity review")
        covered = set()
        valid = set()
        for cid, match in matches.items():
            _keys(match, ("candidate_id", "reference_class_id", "source_consistent"), "candidate match")
            _require(type(match["source_consistent"]) is bool, "explicit source consistency required")
            _require(match["reference_class_id"] is None or match["reference_class_id"] in ref_ids, "unknown reference class")
            if match["source_consistent"]:
                _require(match["reference_class_id"] is not None, "valid candidate needs reviewed reference class")
                valid.add(cid)
                covered.add(match["reference_class_id"])
        if review["reference_complete"]:
            counts["class_recall_eligible_items"] += 1
            counts["reference_classes"] += len(ref_ids)
            counts["covered_reference_classes"] += len(covered)
            counts["fully_covered_nonempty_items"] += bool(ref_ids) and covered == set(ref_ids)
            counts["nonempty_reference_items"] += bool(ref_ids)
        refutations = {jid for jid, j in judgments.items() if j["verdict"] == "refute"}
        labels = _unique(review["refutation_labels"], "judgment_id", "refutation labels")
        _require(set(labels) == refutations, "all refutations need reviewed labels")
        for label in labels.values():
            _keys(label, ("judgment_id", "sound"), "refutation review")
            _require(type(label["sound"]) is bool, "explicit soundness label required")
        counts["reviewed_refutations"] += len(labels)
        counts["false_refutations"] += sum(not label["sound"] for label in labels.values())
        falsely_eliminated = {judgments[jid]["candidate_id"] for jid in refutations if judgments[jid]["candidate_id"] in valid}
        _require(all(not labels[jid]["sound"] for jid in refutations if judgments[jid]["candidate_id"] in valid), "valid candidate refutation cannot be labeled sound")
        counts["questions_with_false_elimination"] += bool(falsely_eliminated)
        hc["false_elimination"] += bool(falsely_eliminated)
    def ratio(numerator, denominator):
        return counts[numerator] / counts[denominator] if counts[denominator] else None
    complete_review = counts["qualified_review_items"] == counts["scheduled"] and counts["scheduled"] > 0
    return {"schema": SCHEMA, "status": "descriptive_development_only", "counts": dict(counts),
            "proposal_development_gate": proposal_development_gate(
                counts, ratio("covered_reference_classes", "reference_classes"),
                ratio("fully_covered_nonempty_items", "nonempty_reference_items"), manifest["population"]),
            "class_recall": ratio("covered_reference_classes", "reference_classes"),
            "full_class_coverage_rate": ratio("fully_covered_nonempty_items", "nonempty_reference_items"),
            "false_refutation_rate": ratio("false_refutations", "reviewed_refutations"),
            "question_false_elimination_rate": ratio("questions_with_false_elimination", "qualified_review_items"),
            "answer_rate": ratio("reviewed_emissions", "scheduled") if complete_review else None,
            "joint_bad_and_emit": ratio("reviewed_bad_emissions", "scheduled") if complete_review else None,
            "selective_risk": ratio("reviewed_bad_emissions", "reviewed_emissions") if complete_review else None,
            "all_scheduled_items_reviewed": complete_review,
            "histories": {key: dict(value) for key, value in sorted(history.items())},
            "warning": "Audit fields record external assertions; this software proves neither semantic completeness nor entailment."}

"""Offline exact joint scoring; no file, model, source or network access.

Reference admission remains external. This module never constructs a reference
from an output. All inputs must already have their own frozen provenance.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from .reader_binding_v18 import (
    ASPECTS, BindingError, EvidencePack, _aspect_value, _canonical, _dump,
    _keys, _load, _merge_quantities, _scope, render_proposal,
    score_direct_quantities, validate_direct_output,
)

PROJECTION_SCHEMA = "reader_joint_projection_v19"
REFERENCE_SCHEMA = "reader_joint_reference_v19"
EXECUTION_STATES = ("completed", "failed", "unattempted", "blocked_after_stop")


def _require(condition, code):
    if not condition:
        raise BindingError(code)


def _handles(value, code, *, nonempty=True):
    _require(isinstance(value, list) and (bool(value) or not nonempty), code)
    _require(all(isinstance(x, str) for x in value), code)
    _require(len(value) == len(set(value)), code)
    return set(value)


def _raw_digest(raw):
    if isinstance(raw, str):
        return hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()
    try:
        return hashlib.sha256(_dump(raw).encode("utf-8")).hexdigest()
    except BindingError:
        return None


def validate_reference(pack, reference):
    """Validate exact target/alias/citation declarations, not semantic admission.

    Scope and value must match every whitelisted occurrence. The frozen semantic
    reviewers, not this function, decide that the whitelist is complete/sound.
    Citation alternatives are correlated six-aspect sets, not an aspect-wise mix.
    """
    _require(isinstance(pack, EvidencePack), "EvidencePack_required")
    ref = _load(reference)
    _keys(ref, ("schema_version", "expected_action", "claims"), "reference_shape")
    _require(ref["schema_version"] == REFERENCE_SCHEMA, "reference_schema")
    _require(ref["expected_action"] in ("answered", "insufficient"), "stage_A_reference_action")
    _require(isinstance(ref["claims"], list), "reference_claims")
    _require((ref["expected_action"] == "answered" and len(ref["claims"]) in (1, 2)) or
             (ref["expected_action"] == "insufficient" and not ref["claims"]), "reference_claim_count")
    data = pack.snapshot()
    facts = {f["handle"]: f for f in data["facts"]}
    witnesses = {w["handle"]: w for w in data["witnesses"]}
    targets = set()
    for claim in ref["claims"]:
        _keys(claim, ("value", "scope", "realizations"), "reference_claim_shape")
        _require(_canonical(claim["value"]), "reference_value")
        _keys(claim["scope"], ASPECTS, "reference_scope")
        for aspect in ASPECTS:
            _require(claim["scope"][aspect] is not None, "reference_unknown_scope")
            _aspect_value(aspect, claim["scope"][aspect])
        target = _dump({"value": claim["value"], "scope": claim["scope"]})
        _require(target not in targets, "duplicate_reference_target")
        targets.add(target)
        _require(isinstance(claim["realizations"], list) and bool(claim["realizations"]),
                 "reference_realizations")
        groups = set()
        for realization in claim["realizations"]:
            _keys(realization, ("occurrence_handles", "allowed_citations", "sufficient_citation_sets"),
                  "reference_realization_shape")
            handles = _handles(realization["occurrence_handles"], "reference_occurrences")
            _require(tuple(sorted(handles)) not in groups, "duplicate_reference_group")
            groups.add(tuple(sorted(handles)))
            _require(handles <= set(facts), "reference_unobserved_occurrence")
            _require(all(facts[h]["value"] == claim["value"] and _scope(facts[h]) == claim["scope"]
                         for h in handles), "reference_alias_scope_or_value")
            allowed = realization["allowed_citations"]
            _keys(allowed, ASPECTS, "reference_allowed_citations")
            for aspect in ASPECTS:
                citations = _handles(allowed[aspect], "reference_allowed_citation_set")
                available = {w for h in handles for w in facts[h]["bindings"][aspect]["witnesses"]}
                _require(citations <= available and all(w in witnesses and
                         witnesses[w]["aspect"] == aspect for w in citations), "reference_citation_support")
            options = realization["sufficient_citation_sets"]
            _require(isinstance(options, list) and bool(options), "reference_sufficient_sets")
            seen_options = set()
            for option in options:
                _keys(option, ASPECTS, "reference_sufficient_set_shape")
                for aspect in ASPECTS:
                    needed = _handles(option[aspect], "reference_sufficient_citations")
                    _require(needed <= set(allowed[aspect]), "reference_sufficient_outside_allowed")
                key = tuple(tuple(sorted(option[a])) for a in ASPECTS)
                _require(key not in seen_options, "duplicate_reference_sufficient_set")
                seen_options.add(key)
    return ref


def _projection(data, action, quantities):
    witnesses = {w["handle"]: w for w in data["witnesses"]}
    merged = _merge_quantities(quantities)
    rows = []
    for key in sorted(merged):
        quantity = merged[key]
        rows.append({"value": quantity["value"], "scope": deepcopy(quantity["scope"]),
                     "occurrence_handles": sorted(quantity["occurrence_handles"]),
                     "citations": {a: [{"handle": h, "text": witnesses[h]["text"]}
                                       for h in sorted(quantity["binding_witnesses"][a])]
                                   for a in ASPECTS}})
    return {"schema_version": PROJECTION_SCHEMA, "action": action, "quantities": rows}


def project_output(pack, output, *, output_kind, execution_status="completed", output_limited=False):
    """Return a common grader view plus a separate mechanical audit.

    output_kind is proposal (B0/B1) or direct (B2); it is not copied to the view
    or audit. Hypotheses, finalizer reasons, unknown flags and clarify-aspect
    lists are deliberately absent from the common view. Raw outputs stay outside.
    """
    _require(isinstance(pack, EvidencePack), "EvidencePack_required")
    _require(output_kind in ("proposal", "direct"), "output_kind")
    _require(execution_status in EXECUTION_STATES, "execution_status")
    _require(type(output_limited) is bool, "output_limit_flag")
    data = pack.snapshot()
    audit = {"execution_status": execution_status, "output_limited": output_limited,
             "raw_sha256": _raw_digest(output), "schema_valid": None,
             "raw_emitted_quantity_count": None, "normalized_quantity_count": 0,
             "collapsed_duplicate_quantity_count": None,
             "exposed_unsupported_quantity_count": None,
             "material_answer_status": "not_executed"}
    if execution_status in ("unattempted", "blocked_after_stop"):
        _require(output is None and not output_limited, "unattempted_cell_has_observed_output")
        return {"projection": _projection(data, "not_executed", []), "audit": audit}
    if execution_status == "failed":
        # A transport/cleanup/accounting failure may still have produced partial
        # or complete raw content. Keep its row/hash; do not parse away uncertainty
        # or discard it as if no material output existed.
        if output is not None:
            audit["material_answer_status"] = "unresolved_requires_review"
        return {"projection": _projection(data, "execution_failed", []), "audit": audit}
    quantities, unsupported = [], 0
    try:
        if output_kind == "proposal":
            result = render_proposal(pack, output)
            if result["action"] == "invalid_output":
                raise BindingError("invalid_proposal")
            action = result["action"]
            quantities = result["quantities"]
            # Proposals are not user-facing numeric answers. This count is the
            # finalizer's emitted quantities, not claim/hypothesis trace length.
            raw_count = len(quantities)
        else:
            validated = validate_direct_output(pack, output)
            result = score_direct_quantities(pack, validated)
            action = validated["action"]
            raw_count = len(validated["quantities"])
            for q, check in zip(validated["quantities"], result["checks"]):
                quantities.append({"value": q["value"], "scope": check["scope"],
                                   "occurrence_handles": check["candidate_handles"],
                                   "binding_witnesses": q["binding_witnesses"]})
                unsupported += int(not (check["support_complete"] and check["value_matches"]))
        projection = _projection(data, action, quantities)
        audit.update(schema_valid=True, raw_emitted_quantity_count=raw_count,
                     normalized_quantity_count=len(projection["quantities"]),
                     collapsed_duplicate_quantity_count=raw_count - len(projection["quantities"]),
                     exposed_unsupported_quantity_count=unsupported,
                     material_answer_status="present" if quantities else "none")
    except BindingError:
        projection = _projection(data, "invalid_output", [])
        audit.update(schema_valid=False, material_answer_status="unresolved_requires_review")
    return {"projection": projection, "audit": audit}


def _matches(quantity, target):
    if quantity["value"] != target["value"] or quantity["scope"] != target["scope"]:
        return False
    cited = {a: {w["handle"] for w in quantity["citations"][a]} for a in ASPECTS}
    for realization in target["realizations"]:
        if set(quantity["occurrence_handles"]) != set(realization["occurrence_handles"]):
            continue
        if any(not cited[a] <= set(realization["allowed_citations"][a]) for a in ASPECTS):
            continue
        if any(all(set(option[a]) <= cited[a] for a in ASPECTS)
               for option in realization["sufficient_citation_sets"]):
            return True
    return False


def score_output(pack, output, reference, *, output_kind, execution_status="completed", output_limited=False):
    """Exact stage-A score. Reference errors raise; invalid outputs remain rows."""
    ref = validate_reference(pack, reference)
    projected = project_output(pack, output, output_kind=output_kind,
                               execution_status=execution_status, output_limited=output_limited)
    view, audit = projected["projection"], projected["audit"]
    quantities, targets = view["quantities"], ref["claims"]
    # References reject duplicate exact scope/value targets and projection merges
    # exact aliases, so a matching quantity cannot stand in for two target claims.
    matches = [(i, j) for i, q in enumerate(quantities) for j, t in enumerate(targets) if _matches(q, t)]
    matched_q, matched_targets = {i for i, _ in matches}, {j for _, j in matches}
    evaluable = execution_status == "completed" and audit["schema_valid"] is True
    action_correct = evaluable and view["action"] == ref["expected_action"]
    support_correct = evaluable and audit["exposed_unsupported_quantity_count"] == 0
    quantities_correct = len(matched_q) == len(quantities) and len(matched_targets) == len(targets)
    joint = bool(action_correct and support_correct and quantities_correct and not output_limited)
    material = audit["material_answer_status"]
    unsupported_answer = None
    if evaluable:
        unsupported_answer = bool(audit["exposed_unsupported_quantity_count"] or
                                  (ref["expected_action"] == "insufficient" and quantities))
    score = {"schema_version": "reader_joint_score_v19", "joint_correct": joint,
             "expected_action": ref["expected_action"], "observed_action": view["action"],
             "action_correct": bool(action_correct), "requested_claim_count": len(targets),
             "matched_quantity_claim_count": len(matched_targets),
             "missing_requested_claim_count": len(targets) - len(matched_targets),
             "unmatched_material_quantity_count": len(quantities) - len(matched_q) if evaluable else None,
             "unsupported_material_answer": unsupported_answer,
             "correct_insufficient": bool(joint and ref["expected_action"] == "insufficient"),
             "safe_incorrect_clarification": bool(evaluable and view["action"] == "clarify" and
                                                    not quantities and not output_limited),
             "material_answer_status": material, "execution_status": execution_status,
             "schema_valid": audit["schema_valid"], "output_limited": output_limited}
    return {**projected, "score": score}


def blind_permutation(records, *, seed):
    """Permutation of already computed common views, with a private ID mapping.

    Freeze seed and ordered input IDs before grading. Sorting uses a length-safe
    canonical JSON hash, not output content or arm. Neither original IDs nor seed
    are included in grader records. This is an ordering measure, not proof that
    content or citation patterns are perfectly indistinguishable.
    """
    _require(isinstance(seed, str) and bool(seed), "permutation_seed")
    _require(isinstance(records, list), "permutation_records")
    ids = []
    checked = []
    for record in records:
        _keys(record, ("output_id", "projection"), "permutation_record")
        _require(isinstance(record["output_id"], str) and bool(record["output_id"]), "permutation_id")
        view = _load(record["projection"])
        _keys(view, ("schema_version", "action", "quantities"), "projection_shape")
        _require(view["schema_version"] == PROJECTION_SCHEMA, "projection_schema")
        _require(view["action"] in ("answered", "clarify", "insufficient", "invalid_output", "not_executed", "execution_failed"),
                 "projection_action")
        _require(isinstance(view["quantities"], list), "projection_quantities")
        # Only outputs of project_output should enter. Reject injected method,
        # hypothesis, reason-code or arbitrary fields at every exported level.
        for quantity in view["quantities"]:
            _keys(quantity, ("value", "scope", "occurrence_handles", "citations"), "projection_quantity_shape")
            _require(_canonical(quantity["value"]), "projection_value")
            _keys(quantity["scope"], ASPECTS, "projection_scope")
            for aspect in ASPECTS:
                if quantity["scope"][aspect] is not None:
                    _aspect_value(aspect, quantity["scope"][aspect])
            _handles(quantity["occurrence_handles"], "projection_occurrences")
            _keys(quantity["citations"], ASPECTS, "projection_citations")
            for aspect in ASPECTS:
                _require(isinstance(quantity["citations"][aspect], list), "projection_citation_list")
                for citation in quantity["citations"][aspect]:
                    _keys(citation, ("handle", "text"), "projection_citation_shape")
                    _require(isinstance(citation["handle"], str) and isinstance(citation["text"], str),
                             "projection_citation_text")
        ids.append(record["output_id"])
        checked.append({"output_id": record["output_id"], "projection": view})
    _require(len(ids) == len(set(ids)), "duplicate_permutation_id")
    key = lambda r: (hashlib.sha256(_dump([seed, r["output_id"]]).encode("utf-8")).hexdigest(), r["output_id"])
    ordered = sorted(checked, key=key)
    grader, mapping = [], []
    for index, record in enumerate(ordered):
        opaque = "o" + str(index + 1).zfill(6)
        grader.append({"output_id": opaque, "projection": record["projection"]})
        mapping.append({"output_id": opaque, "original_output_id": record["output_id"]})
    return {"grader_records": grader, "private_mapping": mapping,
            "manifest": {"schema_version": "reader_grader_permutation_v19",
                         "seed_sha256": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
                         "ordered_input_ids_sha256": hashlib.sha256(_dump(ids).encode("utf-8")).hexdigest(),
                         "private_mapping_sha256": hashlib.sha256(_dump(mapping).encode("utf-8")).hexdigest(),
                         "record_count": len(records)}}

"""Pure question-free registry overlay; never emits/adopts a reader evidence pack.

Inputs are already frozen in-memory objects. This module does no file, source,
model or network I/O. Supplied semantic decisions remain attributable review
records, not facts mechanically certified by a status flag.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .reader_binding_v18 import ASPECTS, BindingError, EvidencePack, _aspect_value, _keys, _scope


def digest(value):
    """Canonical JSON object digest, distinct from an external file-byte digest."""
    try:
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise BindingError("noncanonical_bridge_input") from None
    return hashlib.sha256(raw).hexdigest()


def _require(condition, code):
    if not condition:
        raise BindingError(code)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _anchor_key(source_sha256, locator):
    if not isinstance(locator, dict) or not isinstance(locator.get("anchor"), dict):
        return None
    anchor = locator["anchor"]
    start, stop = anchor.get("byte_start"), anchor.get("byte_stop")
    path = locator.get("dom_path")
    if (not _hash(source_sha256) or type(start) is not int or type(stop) is not int or
        start < 0 or stop <= start or not _hash(anchor.get("span_sha256")) or
        not isinstance(path, str) or not path):
        return None
    return source_sha256, start, stop, anchor["span_sha256"], path


def anchor_identity(anchor):
    """Validate a flat source-SHA/span/DOM anchor and return its exact identity.

    Required fields are source_sha256, byte_start, byte_stop, span_sha256 and
    dom_path. Additional original provenance fields are not identity shortcuts.
    This checks representation, not the corresponding source bytes.
    """
    _require(isinstance(anchor, dict), "invalid_exact_anchor")
    key = _anchor_key(anchor.get("source_sha256"), {"anchor": anchor, "dom_path": anchor.get("dom_path")})
    _require(key is not None, "invalid_exact_anchor")
    return key


def exact_anchor_join(typed_entries, occurrences, *, source_sha256):
    """One result per original positional entry; no value/text/nearest join.

    Bound occurrence identity must uniquely match the typed entry at its inherited
    position. Duplicate anchor matches, wrong source/path/span, missing locator or
    reordered positional identity remain explicit unresolved results. No entries
    are merged or discarded, and no semantic mapping is performed.
    """
    _require(isinstance(typed_entries, list) and isinstance(occurrences, list) and
             len(typed_entries) == len(occurrences), "complete_entry_population_required")
    _require(_hash(source_sha256), "source_digest")
    index = {}
    for ordinal, entry in enumerate(typed_entries):
        anchor = entry.get("anchor", {})
        key = _anchor_key(anchor.get("source_sha256"), {"anchor": anchor, "dom_path": anchor.get("dom_path")})
        if key is not None:
            index.setdefault(key, []).append(ordinal)
    rows, seen = [], set()
    for ordinal, occurrence in enumerate(occurrences):
        handle = occurrence.get("occurrence_handle")
        _require(isinstance(handle, str) and handle and handle not in seen, "occurrence_handle_population")
        seen.add(handle)
        _require(_hash(occurrence.get("entry_sha256")), "occurrence_entry_digest")
        row = {"occurrence_handle": handle, "entry_sha256": occurrence["entry_sha256"],
               "parent_fact_handle": typed_entries[ordinal].get("handle"),
               "join_status": "unmatched", "matched_parent_fact_handles": [], "reason": None}
        if occurrence.get("locator_status") != "bound_to_exact_source_node":
            row.update(join_status="unresolved_locator", reason="projection_locator_unresolved")
        elif occurrence.get("source_sha256") != source_sha256:
            row.update(join_status="provenance_mismatch", reason="occurrence_source_digest_mismatch")
        else:
            key = _anchor_key(occurrence.get("source_sha256"), occurrence.get("locator"))
            found = index.get(key, []) if key is not None else []
            row["matched_parent_fact_handles"] = [typed_entries[i].get("handle") for i in found]
            if key is None:
                row.update(join_status="provenance_mismatch", reason="invalid_bound_locator")
            elif not found:
                row["reason"] = "no_exact_typed_anchor"
            elif len(found) != 1:
                row.update(join_status="ambiguous", reason="multiple_exact_typed_anchors")
            elif found[0] != ordinal:
                row.update(join_status="provenance_mismatch", reason="inherited_position_differs")
            else:
                row.update(join_status="exact_unique", reason=None)
        rows.append(row)
    return rows


def _entry_key(entry):
    key = entry["key"]
    _keys(key, ("bundle_handle", "occurrence_handle", "entry_sha256"), "annotation_entry_key")
    _require(all(isinstance(v, str) and v for v in key.values()) and _hash(key["entry_sha256"]),
             "annotation_entry_identity")
    return tuple(key[x] for x in ("bundle_handle", "occurrence_handle", "entry_sha256"))


def _witness_status(witness, projection, blocks, occurrences):
    """Check literal/selector identity only; never infer a semantic attachment."""
    try:
        _require(witness["source_sha256"] == projection["source_sha256"], "witness_source_mismatch")
        block = blocks[witness["block_handle"]]
        _require(witness["block_id"] == block["block_id"] and witness["anchor"] == block["anchor"] and
                 witness["dom_path"] == block["dom_path"], "witness_block_anchor_mismatch")
        selector = witness["selector"]
        kind = selector["kind"]
        if kind == "whole_block":
            _keys(selector, ("kind",), "whole_block_selector")
            text = block["text"]
        elif kind in ("row", "cell"):
            keys = ("kind", "row_index") if kind == "row" else ("kind", "row_index", "cell_index")
            _keys(selector, keys, "table_selector")
            i = selector["row_index"]
            _require(type(i) is int and 0 <= i < len(block["rows"]), "witness_row_index")
            row = block["rows"][i]
            text = row["text"]
            if kind == "cell":
                j = selector["cell_index"]
                _require(type(j) is int and 0 <= j < len(row["cells"]), "witness_cell_index")
                text = row["cells"][j]["text"]
        elif kind == "text_span":
            _keys(selector, ("kind", "text_start", "text_stop"), "text_span_selector")
            text = block["text"]
            if "occurrence_locator" in witness:
                matching = [o for o in occurrences if o.get("locator") == witness["occurrence_locator"] and
                            witness["block_handle"] in o["containing_block_handles"]]
                _require(len(matching) == 1, "occurrence_witness_locator_not_unique")
                occurrence = matching[0]
                _require(occurrence["source_text"]["segments"] == witness.get("source_text_segments"),
                         "occurrence_witness_segments_mismatch")
                text = occurrence["source_text"]["decoded_text"]
            start, stop = selector["text_start"], selector["text_stop"]
            _require(type(start) is int and type(stop) is int and 0 <= start <= stop <= len(text),
                     "witness_text_span")
            text = text[start:stop]
        else:
            raise BindingError("unsupported_witness_selector")
        _require(isinstance(witness["exact_text"], str) and text == witness["exact_text"] and
                 hashlib.sha256(text.encode("utf-8")).hexdigest() == witness["text_sha256"],
                 "witness_literal_mismatch")
        view = next(v for v in projection["whole_view_availability"] if v["block_handle"] == witness["block_handle"])
        _require(view["status"] == "qualified_inspection_view_only", "witness_view_unqualified")
        _require(isinstance(witness["raster_refs"], list) and bool(witness["raster_refs"]), "witness_raster_missing")
        _require(all(r in view["pages"] for r in witness["raster_refs"]), "witness_raster_outside_fixed_view")
        return {"status": "exact_literal_in_fixed_context", "reason": None}
    except (BindingError, KeyError, IndexError, StopIteration, TypeError) as error:
        return {"status": "unresolved_witness", "reason": str(error) if isinstance(error, BindingError)
                else "witness_structure_or_context_unavailable"}


def reconcile_bundle(parent_pack, registry, projection, annotations, decisions, *, expected_digests,
                     projection_artifact_sha256):
    """Retain all entries and materialize explicit review decisions as an overlay.

    No new EvidencePack is emitted. Even complete supplied decisions yield false
    admission/release flags. The caller must independently review actual semantic
    relations and enforce source/role/manifest provenance before later use.
    """
    _require(isinstance(parent_pack, EvidencePack), "EvidencePack_required")
    inputs = {"parent_pack": parent_pack.snapshot(), "registry": registry, "projection": projection,
              "annotations": annotations, "decisions": decisions}
    _keys(expected_digests, inputs, "input_digest_population")
    _require(all(_hash(expected_digests[k]) and digest(v) == expected_digests[k] for k, v in inputs.items()),
             "frozen_input_digest_mismatch")
    inputs = deepcopy(inputs)
    data, registry, projection, annotations, decisions = (inputs[k] for k in inputs)
    _require(registry.get("schema_version") == "question_free_candidate_registry_v17" and
             projection.get("schema_version") == "source_occurrence_projection_v19" and
             annotations.get("schema_version") == "source_occurrence_initial_annotations_v19", "parent_schema")
    bundle, source = registry["bundle_id"], projection["source_sha256"]
    _require(bundle == data["pack_id"] == projection["bundle_handle"] == annotations["bundle_handle"] and
             source == annotations["source_sha256"] and _hash(source), "parent_identity_mismatch")
    _require(annotations.get("source_handle") == registry["source_id"] and
             annotations.get("status") == "initial_review_frozen_pending_reconciliation", "annotation_parent_state")
    _require(projection.get("author_release_allowed") is False and annotations.get("author_release_allowed") is False,
             "unreleased_parent_required")
    _require(projection.get("question_free") is True and projection.get("semantic_mapping_performed") is False,
             "question_free_projection_required")
    _require(_hash(projection_artifact_sha256) and
             annotations.get("projection_sha256") == projection_artifact_sha256, "annotation_projection_digest")
    facts, occurrences, notes = registry["facts"], projection["occurrences"], annotations["entries"]
    _require(isinstance(notes, list) and len(facts) == len(data["facts"]) == len(occurrences) == len(notes),
             "complete_entry_population_required")
    for original, fact in zip(facts, data["facts"]):
        _require(original["handle"] == fact["handle"] and original["normalized_value"] == fact["value"] and
                 original["visible_row_label_candidates"] == fact["labels"], "parent_registry_pack_disagreement")
        _require(original.get("population_binding") is None and fact["bindings"]["population"] is None,
                 "v18_parent_population_must_remain_null")
        for aspect in ("concept", "entity", "period", "unit", "source"):
            binding = fact["bindings"][aspect]
            expected = ({"sha256": source, "version": registry["source_id"]} if aspect == "source"
                        else original["declared_aspects"].get(aspect))
            _require(binding is None or binding["value"] == expected, "parent_declared_aspect_disagreement")
    blocks = projection["whole_source_context"]["blocks"]
    _require(projection["whole_source_context"]["source_sha256"] == source and
             len(blocks) == len(data["blocks"]) == len(projection["block_handles"]), "whole_context_population")
    block_map = {}
    for raw, packed, mapping in zip(blocks, data["blocks"], projection["block_handles"]):
        _require(mapping == {"block_handle": packed["handle"], "block_id": raw["block_id"]} and
                 raw["text"] == packed["text"], "whole_context_changed")
        block_map[packed["handle"]] = raw
    expected_keys = []
    for occurrence in occurrences:
        saved = occurrence["entry_sha256"]
        body = {k: v for k, v in occurrence.items() if k != "entry_sha256"}
        _require(digest(body) == saved, "projection_entry_digest_changed")
        expected_keys.append((bundle, occurrence["occurrence_handle"], saved))
    _require([_entry_key(n) for n in notes] == expected_keys, "annotation_key_order_or_population_changed")
    joins = exact_anchor_join(facts, occurrences, source_sha256=source)
    witnesses, witness_checks = {}, {}
    for witness in annotations["witnesses"]:
        handle = witness.get("witness_id")
        _require(isinstance(handle, str) and handle and handle not in witnesses, "annotation_witness_ids")
        witnesses[handle] = witness
        witness_checks[handle] = _witness_status(witness, projection, block_map, occurrences)
    _require(isinstance(decisions, list), "decisions_list")
    decision_map = {}
    for decision in decisions:
        _keys(decision, ("key", "aspect", "state", "declared_value_sha256", "attachment_refs",
                         "witness_ids", "value", "rationale", "review_record_sha256"), "decision_shape")
        key = _entry_key(decision)
        _require(key in expected_keys and decision["aspect"] in ASPECTS, "decision_target")
        identity = key, decision["aspect"]
        _require(identity not in decision_map, "duplicate_aspect_decision")
        _require(decision["state"] in ("supported", "unresolved", "rejected") and
                 isinstance(decision["rationale"], str) and bool(decision["rationale"].strip()) and
                 _hash(decision["review_record_sha256"]), "decision_review_provenance")
        for field in ("witness_ids",):
            _require(isinstance(decision[field], list) and all(isinstance(x, str) and x for x in decision[field]) and
                     len(decision[field]) == len(set(decision[field])), "decision_reference_list")
        _require(isinstance(decision["attachment_refs"], list), "decision_attachment_refs")
        seen_refs = set()
        for ref in decision["attachment_refs"]:
            _keys(ref, ("attachment_index", "attachment_sha256"), "decision_attachment_ref_shape")
            _require(type(ref["attachment_index"]) is int and ref["attachment_index"] >= 0 and
                     ref["attachment_index"] not in seen_refs and _hash(ref["attachment_sha256"]), "decision_attachment_ref")
            seen_refs.add(ref["attachment_index"])
        decision_map[identity] = decision
    overlay, sidecar = [], []
    for key, original, fact, occurrence, note, joined in zip(expected_keys, facts, data["facts"], occurrences, notes, joins):
        _require(note["disposition"] in ("reviewed", "reviewed_with_unknowns", "unreviewable"), "annotation_disposition")
        _require(note["presentation"]["status"] in ("visible", "not_visibly_exposed", "ambiguous", "unresolved_locator"),
                 "annotation_presentation_state")
        attachment_aspects = set()
        for attachment in note["attachments"]:
            _require(attachment["state"] in ("supported", "ambiguous", "not_stated_in_fixed_projection",
                                             "not_applicable", "unreviewable") and
                     (attachment["literal"] is None or isinstance(attachment["literal"], str)) and
                     isinstance(attachment["rationale"], str) and bool(attachment["rationale"].strip()) and
                     isinstance(attachment["witness_ids"], list) and
                     all(w in witnesses for w in attachment["witness_ids"]), "annotation_attachment_shape")
            attachment_aspects.add(attachment["aspect"])
        _require({"row_or_clause", "column", "reported_entity", "population", "period", "unit"} <= attachment_aspects,
                 "required_attachment_population")
        mappings = {}
        for aspect in ASPECTS:
            declared = _scope(fact)[aspect]
            mapping = {"state": "pending_separate_review", "accepted_value": None,
                       "declared_value_sha256": digest(declared) if declared is not None else None,
                       "source_witness_ids": [], "review_record_sha256": None}
            decision = decision_map.get((key, aspect))
            if decision is not None:
                selected = []
                for ref in decision["attachment_refs"]:
                    index = ref["attachment_index"]
                    _require(index < len(note["attachments"]) and digest(note["attachments"][index]) == ref["attachment_sha256"],
                             "source_attachment_identity_changed")
                    selected.append(note["attachments"][index])
                _require(decision["declared_value_sha256"] == mapping["declared_value_sha256"],
                         "decision_declared_value_changed")
                mapping.update(state=decision["state"], review_record_sha256=decision["review_record_sha256"])
                if decision["state"] != "supported":
                    _require(decision["value"] is None, "unresolved_mapping_must_stay_null")
                else:
                    _require(joined["join_status"] == "exact_unique", "supported_mapping_requires_exact_join")
                    _require(note["presentation"]["status"] == "visible", "supported_mapping_requires_visible_occurrence")
                    _require(occurrence.get("source_text") is not None and
                             note["presentation"]["literal"] == occurrence["source_text"]["decoded_text"],
                             "presentation_literal_mismatch")
                    _require(bool(selected) and bool(decision["witness_ids"]),
                             "supported_mapping_requires_source_attachments")
                    _require(all(a["state"] == "supported" and bool(a["witness_ids"]) for a in selected),
                             "source_attachment_unresolved")
                    available = {w for a in selected for w in a["witness_ids"]}
                    _require(set(decision["witness_ids"]) == available and
                             all(w in witnesses and witness_checks[w]["status"] == "exact_literal_in_fixed_context"
                                 for w in decision["witness_ids"]), "source_witness_unresolved")
                    _aspect_value(aspect, decision["value"])
                    if aspect == "population":
                        populations = [a for a in selected if a["aspect"] == "population"]
                        _require(any(decision["value"]["label"] == a["literal"] for a in populations),
                                 "population_label_not_explicit")
                        _require(any(decision["value"]["label"] == a["literal"] and
                                     any(a["literal"] in witnesses[w]["exact_text"] for w in a["witness_ids"])
                                     for a in populations), "population_literal_not_in_selected_source_witness")
                        _require(decision["value"]["dimensions"] == original["declared_aspects"]["dimensions"],
                                 "reported_dimensions_changed")
                    else:
                        _require(declared is not None and decision["value"] == declared, "declared_aspect_not_preserved")
                    mapping.update(accepted_value=deepcopy(decision["value"]), source_witness_ids=list(decision["witness_ids"]))
            mappings[aspect] = mapping
        overlay.append({"key": dict(note["key"]), "parent_fact_handle": original["handle"],
                        "join": joined, "declared_typed_metadata": deepcopy(original),
                        "declared_pack_bindings": deepcopy(fact["bindings"]),
                        "heuristic_label_status": "original_candidates_not_source_certified",
                        "source_annotation_disposition": note["disposition"], "aspect_mappings": mappings})
        sidecar.append({"key": dict(note["key"]), "source_annotation": deepcopy(note),
                        "decisions": [deepcopy(decision_map[(key, a)]) for a in ASPECTS if (key, a) in decision_map]})
    common = {"bundle_handle": bundle, "source_sha256": source, "input_object_digests": dict(expected_digests),
              "projection_artifact_sha256": projection_artifact_sha256,
              "entry_count": len(overlay), "author_release_allowed": False, "bundle_admitted": False,
              "candidate_admitted": False, "candidate_pack_emitted": False,
              "status": "mechanical_overlay_pending_independent_semantic_admission"}
    return {"registry_overlay": {"schema_version": "question_free_registry_overlay_v20", **deepcopy(common), "entries": overlay},
            "provenance_sidecar": {"schema_version": "question_free_registry_sidecar_v20", **deepcopy(common),
                                   "entries": sidecar, "source_witnesses": deepcopy(annotations["witnesses"]),
                                   "witness_checks": witness_checks,
                                   "initial_view_inspections": deepcopy(annotations.get("view_inspections", [])),
                                   "limitations": ["Literal identity does not prove semantic attachment.",
                                                   "Supplied review digests are provenance, not authentication.",
                                                   "No new reader pack, admission or author release is produced."]}}


def verify_population(overlays, expected_key_order):
    """Check the frozen complete key manifest across bundles, without admission.

    The real caller binds all 21 bundles/326 keys before execution; invented
    controls use smaller explicit manifests. This function does not select a
    subset or construct the expected manifest from produced results.
    """
    _require(isinstance(overlays, list) and isinstance(expected_key_order, list), "population_lists")
    expected = [_entry_key({"key": key}) for key in expected_key_order]
    _require(len(expected) == len(set(expected)), "duplicate_expected_entry")
    actual, bundles = [], []
    for overlay in overlays:
        _require(overlay.get("schema_version") == "question_free_registry_overlay_v20", "overlay_schema")
        bundle = overlay["bundle_handle"]
        _require(bundle not in bundles, "duplicate_overlay_bundle")
        bundles.append(bundle)
        _require(overlay["entry_count"] == len(overlay["entries"]), "overlay_count_changed")
        _require(all(overlay.get(flag) is False for flag in ("author_release_allowed", "bundle_admitted",
                                                            "candidate_admitted", "candidate_pack_emitted")),
                 "overlay_cannot_admit")
        keys = [_entry_key(entry) for entry in overlay["entries"]]
        _require(all(k[0] == bundle for k in keys), "overlay_bundle_key_mismatch")
        actual.extend(keys)
    _require(actual == expected, "complete_population_or_order_changed")
    _require(bundles == list(dict.fromkeys(key[0] for key in expected)), "complete_bundle_population_or_order_changed")
    return {"expected_entries": len(expected), "retained_entries": len(actual), "bundle_count": len(bundles),
            "exact_ordered_key_equality": True, "admission_performed": False}

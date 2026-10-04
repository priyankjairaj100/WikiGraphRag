"""Invented registry/projection/annotation controls; no private artifact reads."""
from copy import deepcopy
import hashlib
import unittest

from temporal_state.reader_binding_v18 import ASPECTS, BindingError, EvidencePack
from temporal_state.question_free_registry_bridge_v20 import (
    anchor_identity, digest, exact_anchor_join, reconcile_bundle, verify_population,
)


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture(count=2):
    source = "a" * 64
    text = "Revenue Alpha group 2024 USD " + " ".join(["12"] * count)
    anchor = {"byte_start": 0, "byte_stop": 1000, "span_sha256": "b" * 64}
    block = {"block_id": "table_0", "kind": "table", "dom_path": "/authored/table[1]", "anchor": anchor,
             "text": text, "rows": [{"text": "Revenue 12", "cells": [{"text": "Revenue"}, {"text": "12"}]}]}
    empty = {"block_id": "paragraph_0", "kind": "paragraph", "dom_path": "/authored/p[1]",
             "anchor": {"byte_start": 1001, "byte_stop": 1010, "span_sha256": "c" * 64}, "text": "", "rows": []}
    author = {"schema_version": "source_only_author_staging_v17", "bundle_id": "p00t0", "source_id": "s00",
              "source_sha256": source, "author_release_allowed": False, "identity_card": None,
              "status": "pending_visible_identity_and_complete_bundle_admission", "blocks": [block, empty]}
    values = {"concept": "{urn:authored}Revenue", "entity": {"scheme": "urn:authored", "identifier": "Alpha"},
              "period": {"kind": "duration", "lexemes": {"startDate": "2024-01-01", "endDate": "2024-12-31"}},
              "unit": {"shape": "simple_product", "measures": ["{urn:authored}USD"],
                       "numerator_measures": [], "denominator_measures": []},
              "source": {"sha256": source, "version": "s00"}}
    registry = {"schema_version": "question_free_candidate_registry_v17", "bundle_id": "p00t0", "source_id": "s00",
                "status": "not_admitted_not_for_prediction", "reported_metadata_semantics": "copied_declared_aspects",
                "population_policy": "null_until_separate_question_free_source_review", "facts": []}
    raw_pack = {"schema_version": "reader_evidence_pack_v18", "pack_id": "p00t0",
                "blocks": [{"handle": "b000", "text": text}, {"handle": "b001", "text": ""}], "facts": [], "witnesses": []}
    occurrences = []
    for i in range(count):
        handle = "f" + str(i).zfill(3)
        fact_anchor = {"byte_start": 50 + i * 20, "byte_stop": 60 + i * 20,
                       "span_sha256": text_hash("invented-node-" + str(i)), "dom_path": "/authored/f[" + str(i + 1) + "]",
                       "source_sha256": source, "source_version": "invented physical report"}
        declared = {k: deepcopy(v) for k, v in values.items() if k != "source"}
        declared["dimensions"] = []
        entry = {"handle": handle, "source_fact_ordinal": i, "anchor": fact_anchor, "block_ids": ["table_0"],
                 "numeric_status": "normalized", "binding_status": "resolved", "lexical_text": "12",
                 "normalized_value": "12", "declared_aspects": declared, "visible_row_label_candidates": ["Revenue"],
                 "population_binding": None, "visible_period_unit_scope_verified": False, "issues": []}
        registry["facts"].append(entry)
        bindings = {a: None for a in ASPECTS}
        for a, value in values.items():
            wh = "w" + str(i) + "_" + a
            raw_pack["witnesses"].append({"handle": wh, "aspect": a, "text": "Declared source metadata " + a})
            bindings[a] = {"value": deepcopy(value), "witnesses": [wh]}
        raw_pack["facts"].append({"handle": handle, "value": "12", "labels": ["Revenue"], "bindings": bindings})
        locator = {"dom_path": fact_anchor["dom_path"], "anchor": {k: fact_anchor[k] for k in ("byte_start", "byte_stop", "span_sha256")}}
        occurrence = {"occurrence_handle": "o" + str(i).zfill(4), "source_sha256": source,
                      "locator_status": "bound_to_exact_source_node", "unresolved_reason": None, "locator": locator,
                      "containing_block_handles": ["b000"], "source_text": {"decoded_text": "12", "segments": [
                          {"kind": "text", "anchor": {"byte_start": 52 + i * 20, "byte_stop": 54 + i * 20,
                                                       "span_sha256": text_hash("12")}, "raw_text": "12", "decoded_text": "12"}]},
                      "cell_binding": None, "visibility": {"status": "unverified", "computed_css_evaluated": False,
                                                            "occurrence_visually_reviewed": False, "recognized_cue_kinds": []}}
        occurrence["entry_sha256"] = digest(occurrence)
        occurrences.append(occurrence)
    page = {"page_index": 0, "path": "invented/page-1.png", "bytes": 17, "sha256": "d" * 64}
    projection = {"schema_version": "source_occurrence_projection_v19", "bundle_handle": "p00t0", "source_sha256": source,
                  "author_release_allowed": False, "question_free": True, "semantic_mapping_performed": False,
                  "whole_source_context": author, "block_handles": [{"block_handle": "b000", "block_id": "table_0"},
                                                                       {"block_handle": "b001", "block_id": "paragraph_0"}],
                  "separate_identity_card_reference": None, "occurrences": occurrences,
                  "whole_view_availability": [{"block_handle": "b000", "status": "qualified_inspection_view_only", "pages": [page]},
                                              {"block_handle": "b001", "status": "qualified_inspection_view_only", "pages": [page]}],
                  "whole_view_receipt_bindings": {"invented_view_receipt": "e" * 64}}
    witnesses = []
    for aspect, literal in (("row_or_clause", "Revenue"), ("reported_entity", "Alpha group"),
                            ("population", "Alpha group"), ("period", "2024"), ("unit", "USD")):
        start = text.index(literal)
        witnesses.append({"witness_id": "sw_" + aspect, "source_sha256": source, "block_handle": "b000", "block_id": "table_0",
                          "anchor": deepcopy(anchor), "dom_path": block["dom_path"],
                          "selector": {"kind": "text_span", "text_start": start, "text_stop": start + len(literal)},
                          "exact_text": literal, "text_sha256": text_hash(literal), "raster_refs": [deepcopy(page)]})
    entries = []
    for occurrence in occurrences:
        entries.append({"key": {"bundle_handle": "p00t0", "occurrence_handle": occurrence["occurrence_handle"],
                                "entry_sha256": occurrence["entry_sha256"]},
                        "disposition": "reviewed_with_unknowns", "presentation": {"status": "visible", "literal": "12",
                            "witness_ids": [], "rationale": "Invented explicitly visible occurrence"},
                        "attachments": [{"aspect": w["witness_id"][3:], "state": "supported", "literal": w["exact_text"],
                                         "witness_ids": [w["witness_id"]], "rationale": "Invented explicit attachment", "alternatives": []}
                                        for w in witnesses] + [
                            {"aspect": "column", "state": "not_applicable", "literal": None, "witness_ids": [],
                             "rationale": "Invented row context", "alternatives": []},
                            {"aspect": "currency_identity", "state": "not_stated_in_fixed_projection", "literal": None,
                             "witness_ids": [], "rationale": "Unknown remains unknown", "alternatives": []}],
                        "unknown_reason_codes": ["currency_identity_not_reviewed"]})
    annotations = {"schema_version": "source_occurrence_initial_annotations_v19", "bundle_handle": "p00t0",
                   "source_handle": "s00", "source_sha256": source, "plan_sha256": "f" * 64,
                   "projection_sha256": "9" * 64, "reviewer_role": "invented source-only role",
                   "status": "initial_review_frozen_pending_reconciliation", "author_release_allowed": False,
                   "entries": entries, "witnesses": witnesses, "view_inspections": []}
    return {"parent_pack": raw_pack, "registry": registry, "projection": projection, "annotations": annotations, "decisions": []}


def call(inputs, expected=None):
    expected = expected or {k: digest(v) for k, v in inputs.items()}
    return reconcile_bundle(EvidencePack(inputs["parent_pack"]), inputs["registry"], inputs["projection"],
                            inputs["annotations"], inputs["decisions"], expected_digests=expected,
                            projection_artifact_sha256="9" * 64)


def decision(inputs, aspect="unit", ordinal=0, state="supported"):
    source_aspect = {"concept": "row_or_clause", "entity": "reported_entity", "source": "reported_entity"}.get(aspect, aspect)
    binding = inputs["parent_pack"]["facts"][ordinal]["bindings"][aspect]
    value = deepcopy(binding["value"]) if binding else None
    accepted = {"label": "Alpha group", "dimensions": deepcopy(inputs["registry"]["facts"][ordinal]["declared_aspects"]["dimensions"])} if aspect == "population" else value
    selected = next((i, a) for i, a in enumerate(inputs["annotations"]["entries"][ordinal]["attachments"]) if a["aspect"] == source_aspect)
    return {"key": deepcopy(inputs["annotations"]["entries"][ordinal]["key"]), "aspect": aspect, "state": state,
            "declared_value_sha256": digest(value) if value is not None else None,
            "attachment_refs": [{"attachment_index": selected[0], "attachment_sha256": digest(selected[1])}],
            "witness_ids": ["sw_" + source_aspect], "value": accepted if state == "supported" else None,
            "rationale": "Invented explicit question-free reconciliation", "review_record_sha256": "8" * 64}


def reseal_occurrence(inputs, ordinal=0):
    occurrence = inputs["projection"]["occurrences"][ordinal]
    occurrence["entry_sha256"] = digest({k: v for k, v in occurrence.items() if k != "entry_sha256"})
    inputs["annotations"]["entries"][ordinal]["key"]["entry_sha256"] = occurrence["entry_sha256"]


class ExactJoinTests(unittest.TestCase):
    def test_exact_equal_value_occurrences_keep_distinct_identity(self):
        x = fixture()
        rows = exact_anchor_join(x["registry"]["facts"], x["projection"]["occurrences"], source_sha256="a" * 64)
        self.assertEqual([r["join_status"] for r in rows], ["exact_unique"] * 2)
        self.assertEqual([r["matched_parent_fact_handles"] for r in rows], [["f000"], ["f001"]])

    def test_every_identity_component_matters_without_value_fallback(self):
        for field, value in (("source_sha256", "b" * 64), ("byte_start", 51), ("byte_stop", 61),
                             ("span_sha256", "c" * 64), ("dom_path", "/other[1]")):
            with self.subTest(field=field):
                x = fixture()
                x["registry"]["facts"][0]["anchor"][field] = value
                out = call(x)["registry_overlay"]["entries"]
                self.assertEqual(len(out), 2)
                self.assertNotEqual(out[0]["join"]["join_status"], "exact_unique")
                self.assertTrue(all(m["accepted_value"] is None for m in out[0]["aspect_mappings"].values()))

    def test_duplicate_exact_anchors_remain_ambiguous_not_deduplicated(self):
        x = fixture()
        x["registry"]["facts"][1]["anchor"] = deepcopy(x["registry"]["facts"][0]["anchor"])
        out = call(x)["registry_overlay"]
        self.assertEqual(out["entry_count"], 2)
        self.assertEqual(out["entries"][0]["join"]["join_status"], "ambiguous")
        self.assertEqual(out["entries"][1]["join"]["join_status"], "unmatched")

    def test_reordered_exact_anchors_do_not_silently_reassign_positions(self):
        x = fixture()
        a, b = x["registry"]["facts"]
        a["anchor"], b["anchor"] = b["anchor"], a["anchor"]
        statuses = [r["join"]["join_status"] for r in call(x)["registry_overlay"]["entries"]]
        self.assertEqual(statuses, ["provenance_mismatch", "provenance_mismatch"])

    def test_unresolved_locator_retains_typed_entry_and_annotation_unknowns(self):
        x = fixture()
        x["projection"]["occurrences"][0].update(locator_status="unresolved", locator=None, source_text=None,
                                                containing_block_handles=[], unresolved_reason="invented missing locator")
        reseal_occurrence(x)
        out = call(x)["registry_overlay"]["entries"][0]
        self.assertEqual(out["join"]["join_status"], "unresolved_locator")
        self.assertEqual(out["declared_typed_metadata"], x["registry"]["facts"][0])

    def test_invalid_offsets_cannot_be_boolean_or_empty(self):
        base = fixture()["registry"]["facts"][0]["anchor"]
        for field, value in (("byte_start", True), ("byte_stop", 50), ("span_sha256", "no"), ("dom_path", "")):
            x = dict(base); x[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(BindingError, "invalid_exact_anchor"):
                anchor_identity(x)


class OverlayTests(unittest.TestCase):
    def test_source_supported_flags_never_auto_promote_any_aspect(self):
        x = fixture()
        out = call(x)
        for entry in out["registry_overlay"]["entries"]:
            self.assertTrue(all(m["state"] == "pending_separate_review" and m["accepted_value"] is None
                                for m in entry["aspect_mappings"].values()))
            self.assertEqual(entry["heuristic_label_status"], "original_candidates_not_source_certified")
        self.assertNotIn("candidate_pack", out)
        for component in out.values():
            self.assertTrue(all(component[k] is False for k in ("author_release_allowed", "bundle_admitted", "candidate_admitted", "candidate_pack_emitted")))

    def test_one_explicit_mapping_does_not_promote_other_aspects_or_entries(self):
        x = fixture(); x["decisions"] = [decision(x)]
        rows = call(x)["registry_overlay"]["entries"]
        self.assertEqual(rows[0]["aspect_mappings"]["unit"]["state"], "supported")
        self.assertEqual(rows[0]["aspect_mappings"]["unit"]["accepted_value"], x["registry"]["facts"][0]["declared_aspects"]["unit"])
        self.assertIsNone(rows[0]["aspect_mappings"]["population"]["accepted_value"])
        self.assertTrue(all(v["accepted_value"] is None for v in rows[1]["aspect_mappings"].values()))

    def test_population_requires_explicit_literal_and_preserves_dimensions(self):
        x = fixture(); x["decisions"] = [decision(x, "population")]
        value = call(x)["registry_overlay"]["entries"][0]["aspect_mappings"]["population"]["accepted_value"]
        self.assertEqual(value, {"label": "Alpha group", "dimensions": []})
        x["decisions"][0]["value"]["label"] = "Consolidated parent by default"
        with self.assertRaisesRegex(BindingError, "population_label_not_explicit"):
            call(x)

    def test_reported_dimensions_cannot_be_inferred_or_changed(self):
        x = fixture(); x["decisions"] = [decision(x, "population")]
        x["decisions"][0]["value"]["dimensions"] = [{"default": "invented"}]
        with self.assertRaisesRegex(BindingError, "reported_dimensions_changed"):
            call(x)

    def test_supported_annotation_flag_cannot_invent_population_wording(self):
        x = fixture()
        population = next(a for a in x["annotations"]["entries"][0]["attachments"] if a["aspect"] == "population")
        population["literal"] = "Invented consolidation default absent from source"
        x["decisions"] = [decision(x, "population")]
        x["decisions"][0]["value"]["label"] = population["literal"]
        with self.assertRaisesRegex(BindingError, "population_literal_not_in_selected_source_witness"):
            call(x)

    def test_changed_presentation_literal_stays_in_pending_record_but_cannot_support_mapping(self):
        x = fixture(); x["annotations"]["entries"][0]["presentation"]["literal"] = "13"
        self.assertEqual(call(x)["registry_overlay"]["entry_count"], 2)
        x["decisions"] = [decision(x)]
        with self.assertRaisesRegex(BindingError, "presentation_literal_mismatch"):
            call(x)

    def test_changed_declared_value_cannot_be_certified_by_review_flag(self):
        x = fixture(); x["decisions"] = [decision(x)]
        x["decisions"][0]["value"]["measures"] = ["{urn:authored}EUR"]
        with self.assertRaisesRegex(BindingError, "declared_aspect_not_preserved"):
            call(x)

    def test_mismatched_parent_declared_metadata_is_not_accepted(self):
        for aspect in ("concept", "entity", "period", "unit", "source"):
            x = fixture()
            if aspect == "concept":
                x["parent_pack"]["facts"][0]["bindings"][aspect]["value"] = "{urn:other}Revenue"
            elif aspect == "source":
                x["parent_pack"]["facts"][0]["bindings"][aspect]["value"]["version"] = "other"
            else:
                x["registry"]["facts"][0]["declared_aspects"][aspect] = None
            with self.subTest(aspect=aspect), self.assertRaisesRegex(BindingError, "parent_declared_aspect_disagreement"):
                call(x)

    def test_unresolved_and_rejected_decisions_stay_null(self):
        x = fixture(); x["decisions"] = [decision(x, "period", state="unresolved"), decision(x, "unit", state="rejected")]
        mappings = call(x)["registry_overlay"]["entries"][0]["aspect_mappings"]
        self.assertEqual(mappings["period"]["state"], "unresolved")
        self.assertEqual(mappings["unit"]["state"], "rejected")
        self.assertIsNone(mappings["period"]["accepted_value"])
        x["decisions"][0]["value"] = {"invented": "repair"}
        with self.assertRaisesRegex(BindingError, "unresolved_mapping_must_stay_null"):
            call(x)

    def test_review_provenance_and_exact_join_are_required_for_supported_mapping(self):
        x = fixture(); x["decisions"] = [decision(x)]
        x["decisions"][0]["review_record_sha256"] = None
        with self.assertRaisesRegex(BindingError, "decision_review_provenance"):
            call(x)
        x["decisions"][0]["review_record_sha256"] = "8" * 64
        x["registry"]["facts"][0]["anchor"]["byte_start"] += 1
        with self.assertRaisesRegex(BindingError, "supported_mapping_requires_exact_join"):
            call(x)

    def test_unknown_source_attachment_cannot_support_mapping(self):
        x = fixture(); x["decisions"] = [decision(x)]
        unit = next(a for a in x["annotations"]["entries"][0]["attachments"] if a["aspect"] == "unit")
        unit.update(state="ambiguous", literal=None)
        x["decisions"][0]["attachment_refs"][0]["attachment_sha256"] = digest(unit)
        with self.assertRaisesRegex(BindingError, "source_attachment_unresolved"):
            call(x)

    def test_repeated_attachment_aspects_remain_separate_and_explicitly_selected(self):
        x = fixture()
        attachments = x["annotations"]["entries"][0]["attachments"]
        alternative = deepcopy(next(a for a in attachments if a["aspect"] == "population"))
        alternative.update(state="ambiguous", literal=None, rationale="Distinct unresolved qualification")
        attachments.append(alternative)
        x["decisions"] = [decision(x, "population")]
        out = call(x)
        self.assertEqual(len(out["provenance_sidecar"]["entries"][0]["source_annotation"]["attachments"]), len(attachments))
        self.assertEqual(out["registry_overlay"]["entries"][0]["aspect_mappings"]["population"]["accepted_value"]["label"], "Alpha group")
        x["decisions"][0]["attachment_refs"] = [{"attachment_index": len(attachments) - 1, "attachment_sha256": digest(alternative)}]
        with self.assertRaisesRegex(BindingError, "source_attachment_unresolved"):
            call(x)

    def test_stale_attachment_reference_and_omitted_selected_witness_fail(self):
        x = fixture(); x["decisions"] = [decision(x, "population")]
        x["decisions"][0]["attachment_refs"][0]["attachment_sha256"] = "0" * 64
        with self.assertRaisesRegex(BindingError, "source_attachment_identity_changed"):
            call(x)
        x["decisions"] = [decision(x, "population")]
        row = next((i, a) for i, a in enumerate(x["annotations"]["entries"][0]["attachments"]) if a["aspect"] == "row_or_clause")
        x["decisions"][0]["attachment_refs"].append({"attachment_index": row[0], "attachment_sha256": digest(row[1])})
        with self.assertRaisesRegex(BindingError, "source_witness_unresolved"):
            call(x)

    def test_nonvisible_occurrence_does_not_gain_supported_mapping(self):
        x = fixture(); x["decisions"] = [decision(x)]
        x["annotations"]["entries"][0]["presentation"]["status"] = "not_visibly_exposed"
        with self.assertRaisesRegex(BindingError, "supported_mapping_requires_visible_occurrence"):
            call(x)

    def test_nil_unsupported_empty_and_blank_context_are_retained(self):
        for status in ("nil", "unsupported"):
            x = fixture()
            x["registry"]["facts"][0].update(numeric_status=status, normalized_value=None, lexical_text="")
            x["parent_pack"]["facts"][0]["value"] = None
            out = call(x)
            self.assertEqual(out["registry_overlay"]["entry_count"], 2)
            self.assertEqual(out["registry_overlay"]["entries"][0]["declared_typed_metadata"]["numeric_status"], status)
            self.assertIsNone(out["registry_overlay"]["entries"][0]["declared_typed_metadata"]["normalized_value"])
            self.assertEqual(x["parent_pack"]["blocks"][1]["text"], "")

    def test_originals_and_returned_nested_objects_are_detached(self):
        x = fixture(); before = deepcopy(x)
        out = call(x)
        out["registry_overlay"]["entries"][0]["declared_typed_metadata"]["issues"].append("changed")
        out["provenance_sidecar"]["source_witnesses"][0]["exact_text"] = "changed"
        self.assertEqual(x, before)


class WitnessAndPopulationTests(unittest.TestCase):
    def test_block_row_and_cell_witness_forms_are_literal_checked(self):
        for selector, literal in (({"kind": "whole_block"}, None), ({"kind": "row", "row_index": 0}, "Revenue 12"),
                                  ({"kind": "cell", "row_index": 0, "cell_index": 0}, "Revenue")):
            x = fixture()
            w = x["annotations"]["witnesses"][0]
            w.update(selector=selector, exact_text=literal or x["parent_pack"]["blocks"][0]["text"])
            w["text_sha256"] = text_hash(w["exact_text"])
            checks = call(x)["provenance_sidecar"]["witness_checks"]
            self.assertEqual(checks[w["witness_id"]]["status"], "exact_literal_in_fixed_context")

    def test_occurrence_local_span_preserves_distinct_provenance_form(self):
        x = fixture()
        occurrence = x["projection"]["occurrences"][0]
        w = deepcopy(x["annotations"]["witnesses"][0])
        w.update(witness_id="local_numeric", selector={"kind": "text_span", "text_start": 0, "text_stop": 2},
                 exact_text="12", text_sha256=text_hash("12"), occurrence_locator=deepcopy(occurrence["locator"]),
                 source_text_segments=deepcopy(occurrence["source_text"]["segments"]))
        x["annotations"]["witnesses"].append(w)
        self.assertEqual(call(x)["provenance_sidecar"]["witness_checks"]["local_numeric"]["status"], "exact_literal_in_fixed_context")
        w["source_text_segments"][0]["decoded_text"] = "13"
        self.assertEqual(call(x)["provenance_sidecar"]["witness_checks"]["local_numeric"]["status"], "unresolved_witness")

    def test_unresolved_witness_retained_but_cannot_promote_binding(self):
        for change in ("literal", "raster", "row"):
            x = fixture(); w = next(w for w in x["annotations"]["witnesses"] if w["witness_id"] == "sw_unit")
            if change == "literal":
                w["exact_text"] = "EUR"
            elif change == "raster":
                w["raster_refs"][0]["sha256"] = "0" * 64
            else:
                w["selector"] = {"kind": "row", "row_index": -1}
            with self.subTest(change=change):
                self.assertEqual(call(x)["provenance_sidecar"]["witness_checks"]["sw_unit"]["status"], "unresolved_witness")
                x["decisions"] = [decision(x)]
                with self.assertRaisesRegex(BindingError, "source_witness_unresolved"):
                    call(x)

    def test_object_and_physical_file_hashes_are_distinct_bindings(self):
        x = fixture()
        self.assertNotEqual(digest(x["projection"]), x["annotations"]["projection_sha256"])
        out = call(x)["registry_overlay"]
        self.assertEqual(out["projection_artifact_sha256"], "9" * 64)
        self.assertEqual(out["input_object_digests"]["projection"], digest(x["projection"]))
        x["annotations"]["projection_sha256"] = "7" * 64
        with self.assertRaisesRegex(BindingError, "annotation_projection_digest"):
            call(x)

    def test_frozen_digest_change_fails_before_reconciliation(self):
        x = fixture(); expected = {k: digest(v) for k, v in x.items()}
        x["annotations"]["entries"][0]["unknown_reason_codes"].append("new")
        with self.assertRaisesRegex(BindingError, "frozen_input_digest_mismatch"):
            call(x, expected)

    def test_omission_annotation_reordering_and_changed_entry_digest_fail(self):
        for change in ("omission", "order", "digest"):
            x = fixture()
            if change == "omission":
                x["annotations"]["entries"].pop()
            elif change == "order":
                x["annotations"]["entries"].reverse()
            else:
                x["projection"]["occurrences"][0]["source_text"]["decoded_text"] = "13"
            with self.subTest(change=change), self.assertRaises(BindingError):
                call(x)

    def test_complete_population_checker_never_drops_unknowns_or_extra_rows(self):
        x = fixture(); overlay = call(x)["registry_overlay"]
        expected = [deepcopy(n["key"]) for n in x["annotations"]["entries"]]
        receipt = verify_population([overlay], expected)
        self.assertEqual(receipt["retained_entries"], 2)
        self.assertFalse(receipt["admission_performed"])
        for changed in (expected[:1], list(reversed(expected)), expected + [expected[0]]):
            with self.subTest(changed=changed), self.assertRaises(BindingError):
                verify_population([overlay], changed)
        overlay["bundle_admitted"] = True
        with self.assertRaisesRegex(BindingError, "overlay_cannot_admit"):
            verify_population([overlay], expected)

    def test_synthetic_author_release_flag_cannot_be_imported(self):
        x = fixture(); x["annotations"]["author_release_allowed"] = True
        with self.assertRaisesRegex(BindingError, "unreleased_parent_required"):
            call(x)


if __name__ == "__main__":
    unittest.main()

"""Independent v20 bridge checks using exclusively invented in-memory fixtures.

No natural source, annotation ledger, typed registry, question, model, tokenizer,
or file-producing bridge adapter is an input to this test module.
"""
from copy import deepcopy
import hashlib
import unittest

from temporal_state import question_free_registry_bridge_v20 as bridge
from temporal_state.reader_binding_v18 import ASPECTS, BindingError, EvidencePack


SOURCE = "d" * 64
PHYSICAL_PROJECTION = "e" * 64
ATTACHMENTS = ("row_or_clause", "column", "reported_entity", "population", "period", "unit")


def invented_fixture(count=3, bundle="invented_bundle"):
    """Equal values at different physical anchors, a nil, and exact blank context."""
    scopes = {
        "concept": "{urn:invented}Counter",
        "entity": {"scheme": "urn:invented:entity", "identifier": "fictional-unit"},
        "period": {"kind": "instant", "lexemes": {"instant": "2001-01-01"}},
        "unit": {"shape": "simple_product", "measures": ["{urn:invented}item"],
                 "numerator_measures": [], "denominator_measures": []},
        "source": {"sha256": SOURCE, "version": "invented_source"},
    }
    raw = {"schema_version": "reader_evidence_pack_v18", "pack_id": bundle,
           "blocks": [{"handle": "b0", "text": "Invented counter 7; fictional subset"},
                      {"handle": "b1", "text": " \t\u00a0\n"}, {"handle": "b2", "text": ""}],
           "witnesses": [], "facts": []}
    registry = {"schema_version": "question_free_candidate_registry_v17", "bundle_id": raw["pack_id"],
                "source_id": "invented_source", "status": "not_admitted_not_for_prediction", "facts": []}
    projection = {"schema_version": "source_occurrence_projection_v19", "bundle_handle": raw["pack_id"],
                  "source_sha256": SOURCE, "author_release_allowed": False, "question_free": True,
                  "semantic_mapping_performed": False, "occurrences": [], "block_handles": [],
                  "whole_source_context": {"source_sha256": SOURCE, "blocks": []},
                  "whole_view_availability": []}
    annotations = {"schema_version": "source_occurrence_initial_annotations_v19",
                   "bundle_handle": raw["pack_id"], "source_handle": "invented_source", "source_sha256": SOURCE,
                   "status": "initial_review_frozen_pending_reconciliation", "author_release_allowed": False,
                   "projection_sha256": PHYSICAL_PROJECTION, "entries": [], "witnesses": [], "view_inspections": []}
    for i, packed in enumerate(raw["blocks"]):
        block = {"block_id": "invented_block_" + str(i), "text": packed["text"],
                 "anchor": {"byte_start": i * 100, "byte_stop": i * 100 + 99, "span_sha256": str(i + 1) * 64},
                 "dom_path": "/invented/block[" + str(i + 1) + "]", "rows": []}
        projection["whole_source_context"]["blocks"].append(block)
        projection["block_handles"].append({"block_handle": packed["handle"], "block_id": block["block_id"]})
        projection["whole_view_availability"].append({"block_handle": packed["handle"],
                                                     "status": "qualified_inspection_view_only", "pages": ["invented-raster"]})
    for i in range(count):
        value = ("7", "7", None)[i % 3]
        bindings = dict.fromkeys(ASPECTS)
        for aspect, scope in scopes.items():
            wid = "declared_" + str(i) + "_" + aspect
            raw["witnesses"].append({"handle": wid, "aspect": aspect, "text": "Declared source metadata: invented"})
            bindings[aspect] = {"value": deepcopy(scope), "witnesses": [wid]}
        fact = {"handle": "f" + str(i), "value": value, "labels": ["Invented counter"], "bindings": bindings}
        raw["facts"].append(fact)
        anchor = {"source_sha256": SOURCE, "source_version": "invented_source", "byte_start": 10 + i * 10,
                  "byte_stop": 15 + i * 10, "span_sha256": hashlib.sha256(("invented-span-" + str(i)).encode()).hexdigest(),
                  "dom_path": "/invented/block[1]/value[" + str(i + 1) + "]"}
        registry["facts"].append({"handle": fact["handle"], "anchor": anchor, "source_fact_ordinal": i,
                                  "numeric_status": "normalized" if value else "nil", "normalized_value": value,
                                  "binding_status": "copied", "declared_aspects": {**deepcopy(scopes), "dimensions": []},
                                  "visible_row_label_candidates": fact["labels"], "population_binding": None,
                                  "visible_period_unit_scope_verified": False, "issues": ["invented-retained-status"]})
        occurrence = {"occurrence_handle": "o" + str(i), "source_sha256": SOURCE,
                      "locator_status": "bound_to_exact_source_node", "containing_block_handles": ["b0"],
                      "locator": {"anchor": {k: anchor[k] for k in ("byte_start", "byte_stop", "span_sha256")},
                                  "dom_path": anchor["dom_path"]},
                      "source_text": {"segments": ["invented-segment-" + str(i)], "decoded_text": value or "nil"}}
        occurrence["entry_sha256"] = bridge.digest(occurrence)
        projection["occurrences"].append(occurrence)
        annotations["entries"].append({"key": {"bundle_handle": raw["pack_id"], "occurrence_handle": occurrence["occurrence_handle"],
                                              "entry_sha256": occurrence["entry_sha256"]},
                                      "disposition": "unreviewable" if value is None else "reviewed_with_unknowns",
                                      "presentation": {"status": "not_visibly_exposed" if value is None else "visible", "literal": value},
                                      "attachments": [{"aspect": name, "state": "not_stated_in_fixed_projection",
                                                       "literal": None, "rationale": "Invented unresolved relation.", "witness_ids": []}
                                                      for name in ATTACHMENTS]})
    return {"parent_pack": EvidencePack(raw), "registry": registry, "projection": projection,
            "annotations": annotations, "decisions": []}


def run_fixture(fixture, expected_digests=None):
    objects = {k: v.snapshot() if k == "parent_pack" else v for k, v in fixture.items()}
    return bridge.reconcile_bundle(**fixture, expected_digests=expected_digests or {k: bridge.digest(v) for k, v in objects.items()},
                                   projection_artifact_sha256=PHYSICAL_PROJECTION)


def attachment_refs(fixture, *indexes):
    attachments = fixture["annotations"]["entries"][0]["attachments"]
    return [{"attachment_index": i, "attachment_sha256": bridge.digest(attachments[i])} for i in indexes]


def add_review(fixture, aspect="concept"):
    block = fixture["projection"]["whole_source_context"]["blocks"][0]
    text = block["text"]
    witness = {"witness_id": "invented_reviewed_witness", "source_sha256": SOURCE,
               "block_handle": "b0", "block_id": block["block_id"], "anchor": deepcopy(block["anchor"]),
               "dom_path": block["dom_path"], "selector": {"kind": "whole_block"}, "exact_text": text,
               "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "raster_refs": ["invented-raster"]}
    fixture["annotations"]["witnesses"].append(witness)
    attachment = fixture["annotations"]["entries"][0]["attachments"][0]
    attachment.update(state="supported", literal=text, witness_ids=[witness["witness_id"]])
    declared = fixture["parent_pack"].snapshot()["facts"][0]["bindings"][aspect]["value"]
    decision = {"key": deepcopy(fixture["annotations"]["entries"][0]["key"]), "aspect": aspect, "state": "supported",
                "declared_value_sha256": bridge.digest(declared), "attachment_refs": attachment_refs(fixture, 0),
                "witness_ids": [witness["witness_id"]], "value": deepcopy(declared),
                "rationale": "Explicit invented reviewer correspondence, not mechanical entailment.",
                "review_record_sha256": "f" * 64}
    fixture["decisions"].append(decision)
    return witness, attachment, decision


class IndependentBridgeReviewTests(unittest.TestCase):
    def test_equal_values_nil_and_unknown_relations_are_never_deduplicated_or_promoted(self):
        fixture = invented_fixture()
        before = deepcopy({k: v.snapshot() if k == "parent_pack" else v for k, v in fixture.items()})
        result = run_fixture(fixture)
        overlay = result["registry_overlay"]
        self.assertEqual(overlay["entry_count"], 3)
        self.assertEqual([e["parent_fact_handle"] for e in overlay["entries"]], ["f0", "f1", "f2"])
        for original, entry in zip(fixture["registry"]["facts"], overlay["entries"]):
            self.assertEqual(entry["declared_typed_metadata"], original)
            self.assertTrue(all(m["accepted_value"] is None and m["state"] == "pending_separate_review"
                                for m in entry["aspect_mappings"].values()))
        self.assertEqual(result["provenance_sidecar"]["entries"][2]["source_annotation"], fixture["annotations"]["entries"][2])
        after = {k: v.snapshot() if k == "parent_pack" else v for k, v in fixture.items()}
        self.assertEqual(before, after)
        self.assertEqual(EvidencePack.from_prompt_view(fixture["parent_pack"].prompt_view()).snapshot(), before["parent_pack"])
        sidecar_before = deepcopy(result["provenance_sidecar"])
        overlay["input_object_digests"]["registry"] = "0" * 64
        overlay["entries"][0]["declared_typed_metadata"]["issues"].append("output-only mutation")
        self.assertEqual(result["provenance_sidecar"], sidecar_before)
        self.assertEqual(fixture["registry"], before["registry"])

    def test_anchor_identity_uses_every_coordinate_and_rejects_equal_value_shortcuts(self):
        fixture = invented_fixture()
        original = fixture["registry"]["facts"]
        occurrences = fixture["projection"]["occurrences"]
        for coordinate, replacement in (("source_sha256", "a" * 64), ("byte_start", 11), ("byte_stop", 16),
                                        ("span_sha256", "a" * 64), ("dom_path", "/invented/other")):
            typed = deepcopy(original)
            typed[0]["anchor"][coordinate] = replacement
            result = bridge.exact_anchor_join(typed, occurrences, source_sha256=SOURCE)
            self.assertEqual(result[0]["join_status"], "unmatched", coordinate)
            self.assertEqual(len(result), 3)
        reordered = bridge.exact_anchor_join(list(reversed(original)), occurrences, source_sha256=SOURCE)
        self.assertEqual([row["join_status"] for row in reordered], ["provenance_mismatch", "exact_unique", "provenance_mismatch"])

    def test_duplicate_physical_anchor_and_unresolved_locator_stay_separate(self):
        fixture = invented_fixture()
        typed = fixture["registry"]["facts"]
        occurrences = fixture["projection"]["occurrences"]
        typed[1]["anchor"] = deepcopy(typed[0]["anchor"])
        occurrences[2]["locator_status"] = "unresolved"
        result = bridge.exact_anchor_join(typed, occurrences, source_sha256=SOURCE)
        self.assertEqual(result[0]["join_status"], "ambiguous")
        self.assertEqual(result[0]["matched_parent_fact_handles"], ["f0", "f1"])
        self.assertEqual(result[1]["join_status"], "unmatched")
        self.assertEqual(result[2]["join_status"], "unresolved_locator")

    def test_physical_projection_hash_is_not_canonical_object_hash(self):
        fixture = invented_fixture()
        self.assertNotEqual(bridge.digest(fixture["projection"]), PHYSICAL_PROJECTION)
        result = run_fixture(fixture)
        self.assertEqual(result["registry_overlay"]["projection_artifact_sha256"], PHYSICAL_PROJECTION)
        fixture["annotations"]["projection_sha256"] = bridge.digest(fixture["projection"])
        with self.assertRaisesRegex(BindingError, "annotation_projection_digest"):
            run_fixture(fixture)

    def test_stale_input_digest_or_missing_occurrence_is_rejected(self):
        fixture = invented_fixture()
        expected = {k: bridge.digest(v.snapshot() if k == "parent_pack" else v) for k, v in fixture.items()}
        fixture["registry"]["facts"][0]["issues"].append("changed after freeze")
        with self.assertRaisesRegex(BindingError, "frozen_input_digest_mismatch"):
            run_fixture(fixture, expected)
        fixture = invented_fixture()
        fixture["annotations"]["entries"].pop()
        with self.assertRaisesRegex(BindingError, "complete_entry_population_required"):
            run_fixture(fixture)

    def test_parent_binding_must_match_typed_registry_even_without_supported_decision(self):
        fixture = invented_fixture()
        fixture["registry"]["facts"][0]["declared_aspects"]["concept"] = "{urn:invented}DifferentCounter"
        with self.assertRaisesRegex(BindingError, "parent_declared_aspect_disagreement"):
            run_fixture(fixture)

    def test_all326_invented_memberships_reject_omission_reorder_and_extra_empty_bundle(self):
        fixtures = [invented_fixture(16 if i < 11 else 15, "invented_bundle_" + str(i)) for i in range(21)]
        expected = [deepcopy(note["key"]) for fixture in fixtures for note in fixture["annotations"]["entries"]]
        overlays = [run_fixture(fixture)["registry_overlay"] for fixture in fixtures]
        report = bridge.verify_population(overlays, expected)
        self.assertEqual(report, {"expected_entries": 326, "retained_entries": 326, "bundle_count": 21,
                                  "exact_ordered_key_equality": True, "admission_performed": False})
        omitted = deepcopy(overlays)
        omitted[0]["entries"].pop()
        omitted[0]["entry_count"] -= 1
        with self.assertRaisesRegex(BindingError, "complete_population_or_order_changed"):
            bridge.verify_population(omitted, expected)
        reordered = deepcopy(overlays)
        reordered[0]["entries"].reverse()
        with self.assertRaisesRegex(BindingError, "complete_population_or_order_changed"):
            bridge.verify_population(reordered, expected)
        empty = deepcopy(overlays[0])
        empty.update(bundle_handle="invented_extra_empty_bundle", entry_count=0, entries=[])
        with self.assertRaises(BindingError):
            bridge.verify_population(overlays + [empty], expected)

    def test_reviewed_literal_alone_does_not_promote_a_mapping(self):
        fixture = invented_fixture()
        add_review(fixture)
        fixture["decisions"].clear()
        result = run_fixture(fixture)
        self.assertEqual(result["provenance_sidecar"]["witness_checks"]["invented_reviewed_witness"]["status"],
                         "exact_literal_in_fixed_context")
        self.assertEqual(result["registry_overlay"]["entries"][0]["aspect_mappings"]["concept"]["state"], "pending_separate_review")

    def test_explicit_supported_decision_still_produces_no_admission_or_reader_pack(self):
        fixture = invented_fixture()
        add_review(fixture)
        result = run_fixture(fixture)
        self.assertEqual(set(result), {"registry_overlay", "provenance_sidecar"})
        self.assertEqual(result["registry_overlay"]["entries"][0]["aspect_mappings"]["concept"]["state"], "supported")
        for part in result.values():
            for flag in ("author_release_allowed", "bundle_admitted", "candidate_admitted", "candidate_pack_emitted"):
                self.assertIs(part[flag], False)
            self.assertEqual(part["status"], "mechanical_overlay_pending_independent_semantic_admission")

    def test_supported_witness_must_match_fixed_source_literal_anchor_and_raster(self):
        for field, bad in (("source_sha256", "a" * 64), ("exact_text", "different but plausible words"),
                           ("raster_refs", ["outside-fixed-raster"]), ("dom_path", "/invented/other"),
                           ("selector", {"kind": "text_span", "text_start": 0, "text_stop": 999})):
            fixture = invented_fixture()
            witness, _, _ = add_review(fixture)
            witness[field] = bad
            with self.subTest(field=field), self.assertRaisesRegex(BindingError, "source_witness_unresolved"):
                run_fixture(fixture)

    def test_unresolved_mapping_cannot_acquire_value_and_unreviewed_attachment_cannot_support(self):
        fixture = invented_fixture()
        _, _, decision = add_review(fixture)
        decision["state"] = "unresolved"
        with self.assertRaisesRegex(BindingError, "unresolved_mapping_must_stay_null"):
            run_fixture(fixture)
        fixture = invented_fixture()
        _, attachment, decision = add_review(fixture)
        attachment["state"] = "ambiguous"
        decision["attachment_refs"] = attachment_refs(fixture, 0)
        with self.assertRaisesRegex(BindingError, "source_attachment_unresolved"):
            run_fixture(fixture)

    def test_reported_dimensions_cannot_be_rewritten_under_population_review(self):
        fixture = invented_fixture()
        _, attachment, decision = add_review(fixture)
        attachment["aspect"] = "population"
        fixture["annotations"]["entries"][0]["attachments"] = [attachment] + [
            {"aspect": name, "state": "not_stated_in_fixed_projection", "literal": None,
             "rationale": "Invented unresolved relation.", "witness_ids": []}
            for name in ATTACHMENTS if name != "population"]
        decision.update(aspect="population", declared_value_sha256=None, attachment_refs=attachment_refs(fixture, 0),
                        value={"label": attachment["literal"], "dimensions": [{"axis": "invented-default"}]})
        with self.assertRaisesRegex(BindingError, "reported_dimensions_changed"):
            run_fixture(fixture)

    def test_population_attachment_must_contribute_a_selected_source_witness(self):
        for attach_separate_witness in (False, True):
            fixture = invented_fixture()
            row_witness, _, decision = add_review(fixture)
            population = next(a for a in fixture["annotations"]["entries"][0]["attachments"] if a["aspect"] == "population")
            population.update(state="supported", literal="fictional subset")
            if attach_separate_witness:
                witness = deepcopy(row_witness)
                witness.update(witness_id="invented_population_witness", exact_text="fictional subset",
                               selector={"kind": "text_span", "text_start": row_witness["exact_text"].index("fictional subset"),
                                         "text_stop": len(row_witness["exact_text"])},
                               text_sha256=hashlib.sha256(b"fictional subset").hexdigest())
                fixture["annotations"]["witnesses"].append(witness)
                population["witness_ids"] = [witness["witness_id"]]
            decision.update(aspect="population", declared_value_sha256=None,
                            attachment_refs=attachment_refs(fixture, 3, 0),
                            value={"label": population["literal"], "dimensions": []})
            with self.subTest(separate_witness=attach_separate_witness), self.assertRaises(BindingError):
                run_fixture(fixture)

    def test_repeated_unknown_attachments_survive_and_review_reference_binds_exact_record(self):
        fixture = invented_fixture()
        extra = deepcopy(fixture["annotations"]["entries"][0]["attachments"][3])
        extra.update(state="ambiguous", rationale="An additional invented population relation remains unresolved.")
        fixture["annotations"]["entries"][0]["attachments"].append(extra)
        result = run_fixture(fixture)
        self.assertEqual(result["provenance_sidecar"]["entries"][0]["source_annotation"]["attachments"],
                         fixture["annotations"]["entries"][0]["attachments"])
        _, attachment, _ = add_review(fixture)
        attachment["rationale"] = "Changed after the explicit attachment reference was sealed."
        with self.assertRaises(BindingError):
            run_fixture(fixture)

    def test_population_label_must_occur_in_its_selected_attachment_source_witness(self):
        fixture = invented_fixture()
        witness, _, decision = add_review(fixture)
        population = fixture["annotations"]["entries"][0]["attachments"][3]
        population.update(state="supported", literal="fictional subset", witness_ids=[witness["witness_id"]])
        decision.update(aspect="population", declared_value_sha256=None, attachment_refs=attachment_refs(fixture, 3),
                        value={"label": "fictional subset", "dimensions": []})
        result = run_fixture(fixture)
        self.assertEqual(result["registry_overlay"]["entries"][0]["aspect_mappings"]["population"]["accepted_value"],
                         decision["value"])
        self.assertFalse(result["registry_overlay"]["bundle_admitted"])
        population["literal"] = "Invented global consolidated perimeter"
        decision["value"]["label"] = population["literal"]
        decision["attachment_refs"] = attachment_refs(fixture, 3)
        with self.assertRaises(BindingError):
            run_fixture(fixture)


if __name__ == "__main__":
    unittest.main()

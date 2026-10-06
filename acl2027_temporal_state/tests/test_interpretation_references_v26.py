"""Authored fixtures for reference validity, not empirical model evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/validate_interpretation_references_v26.py"
SPEC = importlib.util.spec_from_file_location("references_v26", PATH)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def field(state, value=None, evidence="Q"):
    return {"state": state, "accepted_values": [] if value is None else [value],
            "evidence_sets": [[evidence]] if state == "known" else []}


def pack(contents):
    units, rendered = [], []
    for index, text in enumerate(contents, 1):
        eid, sid = f"E{index:02}", f"source-{index}"
        rendered.append(f"[{eid} | {sid}]\n{text}")
        units.append({"evidence_id": eid, "source_id": sid, "source_sha256": validator.text_hash(text),
                      "byte_start": 0, "byte_stop": len(text.encode("utf-8")),
                      "span_sha256": validator.text_hash(text)})
    text = "\n\n".join(rendered)
    return {"text": text, "sha256": validator.text_hash(text),
            "evidence_ids": [unit["evidence_id"] for unit in units], "units": units}


def fixture():
    references, packs, roster = [], [], []
    for index in range(1, 61):
        item = f"v26-{index:03}"
        query = f"When did entity {index} change?"
        c0, c1 = pack(["An initial statement."]), pack(["An initial statement.", "The change happened in 2020."])
        query_hash = validator.text_hash(query)
        packs.append({"item_id": item, "query": query, "query_sha256": query_hash,
                      "status": "ready", "c0": c0, "c1": c1})
        roster.append({"item_id": item, "query_sha256": query_hash})
        fields = {name: field("not_applicable") for name in validator.FIELDS}
        fields.update(entity=field("known", f"entity {index}"), relation=field("known", "change"),
                      time=field("unknown"))
        expanded = deepcopy(fields)
        expanded["time"] = field("known", "2020", "E02")
        references.append({
            "item_id": item, "query_sha256": query_hash, "c0_sha256": c0["sha256"], "c1_sha256": c1["sha256"],
            "eligibility": "eligible_resolved", "required_fields": ["entity", "relation", "time"],
            "c0": {"status": "insufficient", "classes": [{"class_id": "a", "fields": fields}]},
            "c1": {"status": "resolved", "classes": [{"class_id": "a", "fields": expanded}]},
            "limitations": ["Authored software fixture only."], "author_role": "fixture_author",
            "reviewer_role": "fixture_reviewer", "review_receipt_sha256": validator.text_hash("fixture review"),
        })
    return references, packs, roster


class ReferenceValidationBoundaries(unittest.TestCase):
    def setUp(self):
        self.references, self.packs, self.roster = fixture()

    def validate(self):
        return validator.validate_references(self.references, self.packs, self.roster)

    def test_complete_roster_with_partial_and_resolved_views(self):
        result = self.validate()
        self.assertEqual(result["references_count"], 60)
        self.assertTrue(result["all_roster_items_accounted"])
        self.assertFalse(result["reference_entailment_verified"])
        self.assertFalse(result["preprediction_chronology_verified"])

    def test_missing_duplicate_and_foreign_items_fail(self):
        original = deepcopy(self.references)
        self.references.pop()
        with self.assertRaisesRegex(ValueError, "reference_roster_mismatch"):
            self.validate()
        self.references = original + [deepcopy(original[0])]
        with self.assertRaisesRegex(ValueError, "duplicate_reference_id"):
            self.validate()
        self.references = original
        self.references[-1]["item_id"] = "v26-999"
        with self.assertRaisesRegex(ValueError, "reference_roster_mismatch"):
            self.validate()

    def test_excluded_failure_stays_in_all_sixty_accounting(self):
        reference, packet = self.references[-1], self.packs[-1]
        packet["status"] = "preflight_failure"
        for key in ("c0", "c1"):
            packet[key] = pack([])
            reference[key + "_sha256"] = packet[key]["sha256"]
            reference[key] = {"status": "unscorable", "classes": []}
        reference["eligibility"] = "source_access_failure"
        reference["required_fields"] = []
        self.assertEqual(self.validate()["references_count"], 60)
        reference["limitations"] = []
        with self.assertRaisesRegex(ValueError, "exclusion_reason_missing"):
            self.validate()

    def test_refusal_only_reference_cannot_be_scored(self):
        fields = self.references[0]["c0"]["classes"][0]["fields"]
        for name in self.references[0]["required_fields"]:
            fields[name] = field("unknown")
        with self.assertRaisesRegex(ValueError, "required_known_field_missing"):
            self.validate()

    def test_status_requires_matching_unknown_state_and_class_count(self):
        original = deepcopy(self.references[0])
        self.references[0]["c1"]["classes"][0]["fields"]["time"] = field("unknown")
        with self.assertRaisesRegex(ValueError, "resolved_required_unknown"):
            self.validate()
        self.references[0] = deepcopy(original)
        self.references[0]["c0"]["classes"][0]["fields"]["time"] = field("known", "2020", "Q")
        with self.assertRaisesRegex(ValueError, "insufficient_required_unknown_missing"):
            self.validate()
        self.references[0] = deepcopy(original)
        duplicate = deepcopy(self.references[0]["c1"]["classes"][0])
        duplicate["class_id"] = "b"
        self.references[0]["c1"]["classes"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "resolved_class_count"):
            self.validate()

    def test_ambiguous_requires_two_known_incompatible_classes(self):
        reference = self.references[0]
        reference["eligibility"] = "eligible_ambiguous"
        reference["c1"]["status"] = "ambiguous"
        second = deepcopy(reference["c1"]["classes"][0])
        second["class_id"] = "b"
        reference["c1"]["classes"].append(second)
        with self.assertRaisesRegex(ValueError, "ambiguity_without_known_incompatibility"):
            self.validate()
        second["fields"]["time"] = field("known", "2021", "E02")
        self.assertEqual(self.validate()["eligibility_counts"]["eligible_ambiguous"], 1)
        reference["required_fields"].append("scope")
        reference["c0"]["classes"][0]["fields"]["scope"] = field("unknown")
        for cls in reference["c1"]["classes"]:
            cls["fields"]["scope"] = field("unknown")
        # Another unknown premise does not erase observed known incompatibility.
        self.assertEqual(self.validate()["eligibility_counts"]["eligible_ambiguous"], 1)
        second["class_id"] = "a"
        with self.assertRaisesRegex(ValueError, "duplicate_class_id"):
            self.validate()

    def test_c0_cannot_cite_c1_only_or_unselected_evidence(self):
        reference = self.references[0]
        reference["c0"]["classes"][0]["fields"]["entity"]["evidence_sets"] = [["E02"]]
        with self.assertRaisesRegex(ValueError, "unknown_evidence"):
            self.validate()
        reference["c0"]["classes"][0]["fields"]["entity"]["evidence_sets"] = [["Q"]]
        reference["c1"]["classes"][0]["fields"]["time"]["evidence_sets"] = [["E99"]]
        with self.assertRaisesRegex(ValueError, "unknown_evidence"):
            self.validate()

    def test_empty_duplicate_reserved_aliases_and_witnesses_fail(self):
        target = self.references[0]["c1"]["classes"][0]["fields"]["time"]
        for update, error in [
            ({"accepted_values": []}, "known_requires_values_and_witness"),
            ({"accepted_values": ["2020", "２０２０"]}, "duplicate_normalized_alias"),
            ({"accepted_values": ["NA"]}, "reserved_known_alias"),
            ({"evidence_sets": [[]]}, "witness_set"),
            ({"evidence_sets": [["E02", "E02"]]}, "duplicate_witness_id"),
            ({"evidence_sets": [["E02"], ["E02"]]}, "duplicate_witness_set"),
        ]:
            with self.subTest(error=error):
                original = deepcopy(target)
                target.update(update)
                with self.assertRaisesRegex(ValueError, error):
                    self.validate()
                target.clear()
                target.update(original)

    def test_hash_binding_and_unchanged_c0_membership(self):
        reference, packet = self.references[0], self.packs[0]
        reference["c1_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "reference_pack_hash"):
            self.validate()
        reference["c1_sha256"] = packet["c1"]["sha256"]
        packet["c1"]["units"][0]["source_sha256"] = "1" * 64
        with self.assertRaisesRegex(ValueError, "c0_not_fixed_subset_of_c1"):
            self.validate()
        packet["c1"]["units"][0]["source_sha256"] = packet["c0"]["units"][0]["source_sha256"]
        packet["query"] += " changed"
        with self.assertRaisesRegex(ValueError, "packet_query_bytes"):
            self.validate()

    def test_rendered_span_bytes_and_headers_are_bound(self):
        value = pack(["A Unicode café.", "[E99 | embedded]\nThis is source text, not a new unit."])
        self.assertEqual(set(validator.validate_pack(value, "fixture")), {"E01", "E02"})
        value["text"] = value["text"].replace("café", "cafe")
        value["sha256"] = validator.text_hash(value["text"])
        with self.assertRaisesRegex(ValueError, "rendered_span_hash"):
            validator.validate_pack(value, "fixture")

    def test_required_fields_and_exclusion_status_are_fixed(self):
        self.references[0]["required_fields"].append("outside_schema")
        with self.assertRaisesRegex(ValueError, "required_fields"):
            self.validate()
        self.references[0]["required_fields"].pop()
        self.references[0]["eligibility"] = "ineligible_no_temporal_target"
        with self.assertRaisesRegex(ValueError, "excluded_status"):
            self.validate()

    def test_known_witness_union_must_fit_eight_source_links(self):
        fields = {name: field("not_applicable") for name in validator.FIELDS}
        fields["entity"] = field("known", "A")
        fields["time"] = field("known", "2020")
        fields["entity"]["evidence_sets"] = [["E01", "E02", "E03", "E04", "E05"]]
        fields["time"]["evidence_sets"] = [["E06", "E07", "E08", "E09"]]
        view = {"status": "resolved", "classes": [{"class_id": "a", "fields": fields}]}
        allowed = {f"E{index:02}" for index in range(1, 10)}
        with self.assertRaisesRegex(ValueError, "joint_witness_exceeds_output_capacity"):
            validator.validate_view(view, ["entity", "time"], allowed, "fixture")
        fields["time"]["evidence_sets"].append(["E01"])
        validator.validate_view(view, ["entity", "time"], allowed, "fixture")

    def test_json_reader_preserves_duplicate_and_nonfinite_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "references.json"
            path.write_text(json.dumps({"items": self.references}), encoding="utf-8")
            self.assertEqual(len(validator.read_records(path)), 60)
            path.write_text('{"items":[],"items":[]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
                validator.read_records(path)
            path.write_text('[{"item_id":NaN}]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nonfinite_json_number"):
                validator.read_records(path)


if __name__ == "__main__":
    unittest.main()

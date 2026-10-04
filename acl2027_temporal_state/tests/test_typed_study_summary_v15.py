"""Aggregation/provenance boundaries on authored metadata, never natural facts."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts/run_typed_reader_v15.py"
SPEC = importlib.util.spec_from_file_location("typed_study_runner_v15", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def typed(value="17", status="normalized", path="/authored/fact[1]"):
    return {"dom_path": path, "numeric_status": status, "status": status, "normalized_value": value, "binding_status": "reported_aspects_resolved"}


def reference(value="17", status="normalized", path="/authored/fact[1]"):
    return {"dom_path": path, "status": status, "canonical_value": value}


class SummaryBoundaries(unittest.TestCase):
    def test_lookup_contract_failure_cannot_be_reported_complete(self):
        rows = [{"technical_errors": [], "anchor_failures": []} for _ in range(12)]
        for failing_probe in ("seed_candidate_missing", "wrong_source_sha_not_blocked"):
            totals = {"counts": {"count_matches_expected": 12}, "lookup_counts": {failing_probe: 1}}
            self.assertEqual(runner.completion_status(rows, totals, []), "complete_with_technical_failures")

    def test_numeric_normalization_does_not_imply_usable_fact(self):
        left = typed()
        left.update(status="invalid", binding_status="invalid")
        got = runner.compare_records([left], [reference()], 1)
        self.assertEqual(got["counts"]["exact_numeric_agreements"], 1)
        self.assertEqual(got["reader_fact_status_counts"], {"invalid": 1})

    def test_unsupported_reference_never_counts_as_agreement(self):
        got = runner.compare_records([typed(), typed("19")], [reference(), reference(None, "unsupported_format")], 2)
        self.assertEqual(got["counts"]["expected_population"], 2)
        self.assertEqual(got["counts"]["common_normalized_population"], 1)
        self.assertEqual(got["counts"]["exact_numeric_agreements"], 1)
        self.assertEqual(got["reference_status_counts"]["unsupported_format"], 1)

    def test_identical_values_at_different_locations_do_not_agree(self):
        got = runner.compare_records([typed()], [reference(path="/different/fact[1]")], 1)
        self.assertEqual(got["counts"]["alignment_failures"], 1)
        self.assertEqual(got["counts"].get("exact_numeric_agreements", 0), 0)

    def test_failed_source_stays_in_full_population(self):
        failed = runner.compare_records([], [], 7)
        passed = runner.compare_records([typed()], [reference()], 1)
        got = runner.combine_summaries([failed, passed])
        self.assertEqual(got["counts"]["expected_population"], 8)
        self.assertEqual(got["reader_status_counts"]["missing_occurrence"], 7)
        self.assertEqual(got["counts"]["count_matches_expected"], 1)

    def test_extra_occurrences_are_kept_and_count_mismatch_recorded(self):
        got = runner.compare_records([typed(), typed()], [reference()], 1)
        self.assertEqual(got["counts"]["reader_observed"], 2)
        self.assertEqual(got["counts"]["count_matches_expected"], 0)
        self.assertEqual(got["counts"]["missing_occurrence_pairs"], 1)

    def test_nil_is_not_numeric_agreement_or_zero(self):
        got = runner.compare_records([typed(None, "nil"), typed(None, "nil")], [reference(None, "nil"), reference("0")], 2)
        self.assertEqual(got["counts"]["both_nil_no_numeric_comparison"], 1)
        self.assertEqual(got["counts"].get("exact_numeric_agreements", 0), 0)
        self.assertEqual(got["counts"].get("common_normalized_population", 0), 0)

    def test_noncanonical_text_breaks_contract_instead_of_numeric_coercion(self):
        for value in ("17.0", "1e3", "-0", "+17", "017", None):
            with self.subTest(value=value):
                got = runner.compare_records([typed(value)], [reference(value)], 1)
                self.assertEqual(got["counts"]["canonical_contract_failures"], 1)
                self.assertEqual(got["counts"].get("exact_numeric_agreements", 0), 0)

    def test_public_disagreement_has_no_values(self):
        got = runner.compare_records([typed("1787654321")], [reference("9876543219")], 1)
        output = json.dumps(got)
        self.assertNotIn("1787654321", output)
        self.assertNotIn("9876543219", output)
        self.assertEqual(got["counts"]["exact_numeric_disagreements"], 1)

    def test_anchor_cannot_be_repointed_to_equal_text(self):
        body = b"ab ab"
        sha = hashlib.sha256(body).hexdigest()
        expected = {"source_sha256": sha, "dom_path": "/authored", "byte_start": 0, "byte_stop": 2, "span_sha256": hashlib.sha256(b"ab").hexdigest()}
        changed = {**expected, "byte_start": 3, "byte_stop": 5}
        self.assertEqual(runner.verify_anchor(changed, expected, body, sha), "anchor_metadata_mismatch")


class ProvenanceBoundaries(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = self.root / "external"
        self.sources.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        codes = {}
        for name in runner.REQUIRED_CODE:
            codes[name] = self.write(name, "authored code fixture\n")
        design_sha = self.write("docs/typed_reader_study_design_v15.txt", "authored design fixture\n")
        identities, inventory, sources = [], [], []
        for ordinal in range(12):
            name = "authored_" + str(ordinal) + ".html"
            body = b"authored metadata fixture"
            path = self.sources / name
            path.write_bytes(body)
            sha = runner.file_sha(path)
            count = 23640 if ordinal == 0 else 1
            identities.append({"source_path": "original_doc/" + name, "source_sha256": sha, "bytes": len(body)})
            inventory.append({"source_path": name, "counts": {"nonfraction_facts": count}})
            sources.append({"source_path": "original_doc/" + name, "external_filename": name, "expected_sha256": sha, "expected_bytes": len(body), "expected_nonfraction_count": count})
        inputs = {runner.REQUIRED_INPUT[0]: self.write(runner.REQUIRED_INPUT[0], json.dumps({"records": identities})), runner.REQUIRED_INPUT[1]: self.write(runner.REQUIRED_INPUT[1], json.dumps({"records": inventory}))}
        runtime_file = self.runtime / "pinned.py"
        runtime_file.write_text("authored dependency fixture")
        wheel = self.root / "authored.whl"
        wheel.write_bytes(b"authored fixture, not a wheel")
        self.protocol = {"schema_version": "typed_reader_execution_protocol_v15", "frozen_at_utc": "authored-freeze", "code_bindings": codes, "input_bindings": inputs, "design_sha256": design_sha, "reference_runtime": {"path": str(self.runtime), "version": "authored", "files": {"pinned.py": runner.file_sha(runtime_file)}, "additional_files": {str(wheel): runner.file_sha(wheel)}, "wheel_path": str(wheel)}, "sources": sources, "expected_total_nonfraction_count": 23651, "natural_QA_predictions": 0, "lookup_integration": {"max_distinct_keys_per_source": 8, "wrong_source_sha256_probe": True}}

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return runner.file_sha(path)

    def test_preflight_accepts_complete_bound_metadata_without_parsing(self):
        got = runner.validate_protocol(self.protocol, self.sources, self.root)
        self.assertIn(str(self.runtime / "pinned.py"), got)

    def test_last_source_tampering_rejects_entire_preflight(self):
        (self.sources / "authored_11.html").write_text("modified")
        with self.assertRaisesRegex(ValueError, "source integrity mismatch"):
            runner.validate_protocol(self.protocol, self.sources, self.root)

    def test_code_changed_after_freeze_rejected(self):
        (self.root / runner.REQUIRED_CODE[0]).write_text("changed")
        with self.assertRaisesRegex(ValueError, "repository binding digest mismatch"):
            runner.validate_protocol(self.protocol, self.sources, self.root)

    def test_source_swap_rejected_despite_matching_aggregate_counts(self):
        protocol = deepcopy(self.protocol)
        protocol["sources"][1], protocol["sources"][2] = protocol["sources"][2], protocol["sources"][1]
        with self.assertRaisesRegex(ValueError, "historical source population/order/count"):
            runner.validate_protocol(protocol, self.sources, self.root)

    def test_unbound_wheel_rejected(self):
        protocol = deepcopy(self.protocol)
        protocol["reference_runtime"]["additional_files"] = {}
        with self.assertRaisesRegex(ValueError, "wheel lacks"):
            runner.validate_protocol(protocol, self.sources, self.root)

    def test_runtime_escape_rejected(self):
        protocol = deepcopy(self.protocol)
        protocol["reference_runtime"]["files"]["../authored.whl"] = runner.file_sha(self.root / "authored.whl")
        with self.assertRaisesRegex(ValueError, "path escapes"):
            runner.validate_protocol(protocol, self.sources, self.root)


class AuthoredAdapterIntegration(unittest.TestCase):
    def test_anchor_idref_matching_collapses_only_xml_whitespace(self):
        sys.path.insert(0, str(RUNNER_PATH.parent))
        import reference_numeric_v15 as reference_module
        import inventory_inline_xbrl_v14 as inventory
        body = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:x="http://www.xbrl.org/2003/instance"><head/><body><x:context id="&#x9;c&#xA;"/><x:unit id=" u "/><ix:nonFraction contextRef=" c " unitRef="&#xD;u&#x9;">17</ix:nonFraction></body></html>'''
        sha = hashlib.sha256(body).hexdigest()
        tree = reference_module.parse_document(body)
        anchors = runner.expected_anchor_map(body, tree, sha, inventory)
        fact_anchor = next(a for p, a in anchors.items() if p.endswith("nonFraction[1]"))
        # The checker receives exact declared anchors; it must resolve the
        # differently padded ID/IDREF lexemes before comparing those bytes.
        typed_document = {"facts": [{"anchor": fact_anchor, "evidence_anchors": list(anchors.values())}]}
        counts, failures = runner.anchor_audit(body, typed_document, tree, sha, inventory)
        self.assertEqual(counts["context_anchors_verified"], 1)
        self.assertEqual(counts["unit_anchors_verified"], 1)
        self.assertFalse(failures)
        self.assertEqual(runner.collapsed_xml_token("\u00a0c\u00a0"), "\u00a0c\u00a0")

    def test_exact_anchor_and_lookup_join_on_authored_xml(self):
        # Plain decimal: the independent adapter does not call a registry or
        # network. Two authored facts test nil population retention as well.
        sys.path.insert(0, str(RUNNER_PATH.parent))
        sys.path.insert(0, str(RUNNER_PATH.parents[1] / "src"))
        from temporal_state import typed_reader_v15 as implementation
        import reference_numeric_v15 as reference_module
        import inventory_inline_xbrl_v14 as inventory
        body = b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:x="http://www.xbrl.org/2003/instance" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:c="urn:authored:concept" xmlns:u="urn:authored:unit"><head/><body><ix:header><ix:resources><x:context id="c"><x:entity><x:identifier scheme="urn:authored:entity">A</x:identifier></x:entity><x:period><x:instant>2024-12-31</x:instant></x:period></x:context><x:unit id="u"><x:measure>u:USD</x:measure></x:unit></ix:resources></ix:header><ix:nonFraction name="c:R" contextRef="c" unitRef="u" decimals="0">17</ix:nonFraction><ix:nonFraction name="c:N" contextRef="c" unitRef="u" xsi:nil="true"/></body></html>'''
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            sources, external = folder / "sources", folder / "external"
            sources.mkdir(); external.mkdir()
            (sources / "authored.html").write_bytes(body)
            entry = {"source_path": "original_doc/authored.html", "external_filename": "authored.html", "expected_bytes": len(body), "expected_sha256": hashlib.sha256(body).hexdigest(), "expected_nonfraction_count": 2}
            got = runner.run_source(entry, sources, external, implementation, reference_module, inventory, 8)
            self.assertEqual(got["counts"]["exact_numeric_agreements"], 1)
            self.assertEqual(got["counts"]["both_nil_no_numeric_comparison"], 1)
            self.assertEqual(got["anchor_counts"]["fact_anchors_verified"], 2)
            self.assertEqual(got["anchor_counts"]["context_anchors_verified"], 2)
            self.assertEqual(got["anchor_counts"]["unit_anchors_verified"], 2)
            self.assertEqual(got["lookup_counts"]["seed_candidate_included"], 1)
            self.assertEqual(got["lookup_counts"]["wrong_source_sha_blocked"], 1)
            self.assertFalse(got["technical_errors"])
            self.assertFalse(got["anchor_failures"])
            self.assertTrue(Path(got["external_records"]["path"]).exists())


if __name__ == "__main__":
    unittest.main()

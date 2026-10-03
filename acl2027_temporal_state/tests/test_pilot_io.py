"""Self-contained corruption and source-prefix leakage regression checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from temporal_state.pilot_io import (
    BENCHMARK, DEVELOPMENT, REPRESENTATION,
    load_prefix, load_sources, validate_extraction,
)


class PilotIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "sources").mkdir()
        self.manifest_path = self.root / "manifest.json"
        self.prefix_path = self.root / "input.json"
        self.manifest = {"status": DEVELOPMENT, "sources": []}
        texts = {
            "a": ("history_a", "2024-01-02", "É 🧭. Alice is CEO.\n"),
            "a_later": ("history_a", "2024-02-02", "Bob will become CEO.\n"),
            "b": ("history_b", "2024-01-02", "Carol is CEO.\n"),
        }
        for source_id, (history, available, text) in texts.items():
            raw = text.encode("utf-8")
            (self.root / "sources" / f"{source_id}.txt").write_bytes(raw)
            self.manifest["sources"].append({
                "source_id": source_id, "history_id": history,
                "url": f"https://example.invalid/{source_id}",
                "text_path": f"sources/{source_id}.txt",
                "text_sha256": hashlib.sha256(raw).hexdigest(), "text_bytes": len(raw),
                "representation": REPRESENTATION, "admission_status": DEVELOPMENT,
                "operational_available_at": available,
                "reported_publication_date": "2024-01-01",
                "availability_basis": "development_proxy_not_measured_availability",
                "historical_bytes_verified": False,
                "first_public_availability_verified": False,
            })
        self.write_manifest()
        self.input = {"prefixes": {"history_a": "2024-01-02", "history_b": "2024-01-02"},
                      "sources": [self.inline(row) for row in self.manifest["sources"]
                                  if row["source_id"] != "a_later"]}
        self.write_prefix()

    def inline(self, row):
        fields = ("source_id", "history_id", "text_sha256", "reported_publication_date",
                  "operational_available_at", "availability_basis")
        result = {key: row[key] for key in fields}
        result["text"] = (self.root / row["text_path"]).read_text(encoding="utf-8")
        return result

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def write_prefix(self):
        self.prefix_path.write_text(json.dumps(self.input, ensure_ascii=False), encoding="utf-8")

    def prefix(self):
        return load_prefix(self.prefix_path, load_sources(self.manifest_path))

    def extraction(self):
        prefix = self.prefix()
        text = self.input["sources"][0]["text"]
        quote = "Alice is CEO."
        start = text.index(quote)
        return {"pass_id": "test", "input_sha256": prefix.input_sha256,
                "backend": {"model_revision": None, "decoding_settings": None,
                            "pinned": False, "kind": "conversational_model_agent"},
                "assertions": [{"assertion_id": "assert_a", "source_id": "a",
                    "subject": "A", "relation": "chief executive officer", "scope": "corporation",
                    "value": "Alice", "valid_from": None, "valid_to": None,
                    "observed_on": "2024-01-01", "status": "reported", "qualifier": "",
                    "operation": "ASSERT", "target_id": None, "rationale": "Observed role, unknown start.",
                    "evidence": [{"start": start, "end": start + len(quote), "quote": quote}],
                    "context_source_ids": []}],
                "disagreements_or_ambiguities": [], "exclusions": [], "limitations": []}

    def test_operational_date_is_preserved_without_inference(self):
        loaded = load_sources(self.manifest_path)
        self.assertEqual(loaded.sources[0].available_at, "2024-01-02")
        self.assertEqual(loaded.metadata["a"]["reported_publication_date"], "2024-01-01")
        self.assertIn("not_measured", loaded.sources[0].availability_basis)
        del self.manifest["sources"][0]["operational_available_at"]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "operational_available_at"):
            load_sources(self.manifest_path)

    def test_development_cannot_be_promoted_by_purpose_flag(self):
        with self.assertRaisesRegex(ValueError, "not admitted"):
            load_sources(self.manifest_path, purpose="historical_benchmark")
        self.manifest["status"] = BENCHMARK
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "not verified"):
            load_sources(self.manifest_path, purpose="historical_benchmark")

    def test_missing_boolean_verification_is_not_truthy_admission(self):
        self.manifest["status"] = BENCHMARK
        for row in self.manifest["sources"]:
            row.update(admission_status=BENCHMARK, historical_bytes_verified="true",
                       first_public_availability_verified=True)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "not verified"):
            load_sources(self.manifest_path, purpose="historical_benchmark")

    def test_hash_catches_same_length_corruption(self):
        path = self.root / "sources/a.txt"
        path.write_bytes(path.read_bytes().replace(b"Alice", b"Alicf"))
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            load_sources(self.manifest_path)

    def test_size_and_representation_identity(self):
        for field, value, error in [("text_bytes", 1, "byte count"),
                                     ("representation", "raw_html", "representation")]:
            with self.subTest(field=field):
                original = self.manifest["sources"][0][field]
                self.manifest["sources"][0][field] = value
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, error):
                    load_sources(self.manifest_path)
                self.manifest["sources"][0][field] = original

    def test_utf8_is_checked_even_with_valid_digest(self):
        raw = b"\xffnot utf8"
        (self.root / "sources/a.txt").write_bytes(raw)
        self.manifest["sources"][0].update(text_sha256=hashlib.sha256(raw).hexdigest(), text_bytes=len(raw))
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "not UTF-8"):
            load_sources(self.manifest_path)

    def test_path_traversal_absolute_and_symlink_escape(self):
        outside = self.root.parent / (self.root.name + "_outside.txt")
        outside.write_text("outside", encoding="utf-8")
        self.addCleanup(outside.unlink)
        (self.root / "sources/escape.txt").symlink_to(outside)
        for path in ("../outside.txt", str(outside), "sources\\a.txt", "sources/escape.txt"):
            with self.subTest(path=path):
                self.manifest["sources"][0]["text_path"] = path
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, "[Uu]nsafe|escapes"):
                    load_sources(self.manifest_path)

    def test_duplicate_source_ids_and_json_keys(self):
        self.manifest["sources"].append(deepcopy(self.manifest["sources"][0]))
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Duplicate source_id"):
            load_sources(self.manifest_path)
        self.manifest_path.write_text('{"status":"one","status":"two"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            load_sources(self.manifest_path)

    def test_prefix_digest_is_of_original_file_bytes(self):
        prefix = self.prefix()
        self.assertEqual(prefix.input_sha256, hashlib.sha256(self.prefix_path.read_bytes()).hexdigest())
        output = self.extraction()
        with self.prefix_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "exact prefix input bytes"):
            validate_extraction(output, self.prefix())

    def test_prefix_forbids_future_and_cross_history_sources(self):
        self.input["sources"].append(self.inline(self.manifest["sources"][1]))
        self.write_prefix()
        with self.assertRaisesRegex(ValueError, "Future source"):
            self.prefix()
        self.input["sources"].pop()
        self.input["sources"][0]["history_id"] = "history_b"
        self.write_prefix()
        with self.assertRaisesRegex(ValueError, "history mismatch"):
            self.prefix()

    def test_prefix_forbids_rewritten_text_and_metadata(self):
        for field, value, error in [("text", "Alice is CEO", "text differs"),
                                     ("operational_available_at", "2024-01-01", "mismatch")]:
            with self.subTest(field=field):
                old = self.input["sources"][0][field]
                self.input["sources"][0][field] = value
                self.write_prefix()
                with self.assertRaisesRegex(ValueError, error):
                    self.prefix()
                self.input["sources"][0][field] = old

    def test_prefix_requires_complete_eligible_subset(self):
        self.input["sources"].pop()
        self.write_prefix()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.prefix()

    def test_unknown_start_and_negative_status_are_retained(self):
        output = self.extraction()
        output["assertions"][0].update(status="negative", valid_to="2024-01-01")
        validated = validate_extraction(output, self.prefix())
        self.assertIsNone(validated["assertions"][0]["valid_from"])
        self.assertEqual(validated["assertions"][0]["status"], "negative")
        validated["assertions"][0]["value"] = "Changed copy"
        self.assertEqual(output["assertions"][0]["value"], "Alice")

    def test_unicode_offsets_are_not_utf8_byte_offsets(self):
        output = self.extraction()
        self.assertEqual(validate_extraction(output, self.prefix()), output)
        span = output["assertions"][0]["evidence"][0]
        span["start"] += 4  # accented character and astral symbol precede the quote
        span["end"] += 4
        with self.assertRaisesRegex(ValueError, "offsets"):
            validate_extraction(output, self.prefix())

    def test_evidence_must_be_nonempty_and_exact(self):
        output = self.extraction()
        output["assertions"][0]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "nonempty evidence"):
            validate_extraction(output, self.prefix())
        output = self.extraction()
        output["assertions"][0]["evidence"][0]["quote"] = "Bob is CEO."
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_extraction(output, self.prefix())

    def test_context_cannot_leak_later_or_other_history(self):
        for source_id in ("a_later", "b"):
            with self.subTest(source_id=source_id):
                output = self.extraction()
                output["assertions"][0]["context_source_ids"] = [source_id]
                with self.assertRaisesRegex(ValueError, "unavailable in this history"):
                    validate_extraction(output, self.prefix())

    def test_date_interval_status_and_duplicate_assertion_validation(self):
        for update, error in [({"valid_from": "2024-02-30"}, "Invalid valid_from"),
                              ({"valid_from": "2024-01-02", "valid_to": "2024-01-02"}, "positive duration"),
                              ({"status": "positive"}, "Unsupported"),
                              ({"observed_on": "2024-02-01"}, "after")]:
            with self.subTest(update=update):
                output = self.extraction()
                output["assertions"][0].update(update)
                with self.assertRaisesRegex(ValueError, error):
                    validate_extraction(output, self.prefix())
        output = self.extraction()
        output["assertions"].append(deepcopy(output["assertions"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate assertion_id"):
            validate_extraction(output, self.prefix())

    def test_correction_target_missing_dangling_and_cycles(self):
        for target, error in [(None, "requires"), ("missing", "Dangling"), ("assert_a", "cycle")]:
            with self.subTest(target=target):
                output = self.extraction()
                output["assertions"][0].update(operation="CORRECTS", target_id=target)
                with self.assertRaisesRegex(ValueError, error):
                    validate_extraction(output, self.prefix())

    def test_cross_key_targets_are_rejected(self):
        output = self.extraction()
        second = deepcopy(output["assertions"][0])
        second.update(assertion_id="assert_a2", relation="chief financial officer", target_id="assert_a")
        output["assertions"].append(second)
        with self.assertRaisesRegex(ValueError, "another factual key"):
            validate_extraction(output, self.prefix())


if __name__ == "__main__":
    unittest.main()

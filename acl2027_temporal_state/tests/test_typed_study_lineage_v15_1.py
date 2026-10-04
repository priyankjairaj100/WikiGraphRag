"""Authored metadata tests for retaining the failed attempt and amendment scope."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/run_typed_reader_v15_1.py"
SPEC = importlib.util.spec_from_file_location("typed_study_runner_v15_1", PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ParentLineage(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        codes = {name: self.write(name, "authored code") for name in (
            "scripts/reference_numeric_v15.py", "scripts/inventory_inline_xbrl_v14.py",
            "scripts/run_typed_reader_v15.py", "src/temporal_state/typed_reader_v15.py")}
        inputs = {"data/authored_input.json": self.write("data/authored_input.json", "authored metadata")}
        sources = [{"source_path": "authored_" + str(i), "expected_nonfraction_count": 23640 if i == 0 else 1} for i in range(12)]
        self.parent = {"schema_version": "typed_reader_execution_protocol_v15", "sources": sources,
            "expected_total_nonfraction_count": 23651, "design_sha256": "authored design hash",
            "reference_runtime": {"version": "authored pinned runtime"}, "lookup_integration": {"max_distinct_keys_per_source": 8},
            "natural_QA_predictions": 0, "methods_profile": {"selection": "all frozen occurrences", "unsupported": "retain"},
            "input_bindings": inputs, "code_bindings": codes}
        parent_path = "data/authored_parent_protocol.json"
        parent_sha = self.write(parent_path, json.dumps(self.parent))
        self.outcome = {"schema_version": "typed_reader_execution_v15", "status": "complete_with_technical_failures",
            "finished_at_utc": "authored completion", "protocol_sha256": parent_sha,
            "records": [{"source_path": row["source_path"], "technical_errors": [{"component": "reader"}] if i == 0 else []} for i, row in enumerate(sources)]}
        outcome_path = "results/authored_parent_result.json"
        outcome_sha = self.write(outcome_path, json.dumps(self.outcome))
        amendment_path = "docs/typed_reader_encoding_amendment_v15_1.txt"
        amendment_sha = self.write(amendment_path, "authored encoding-only amendment")
        self.protocol = deepcopy(self.parent)
        self.protocol["schema_version"] = "typed_reader_execution_protocol_v15_1"
        self.protocol["parent_protocol"] = {"path": parent_path, "sha256": parent_sha}
        self.protocol["parent_result"] = {"path": outcome_path, "sha256": outcome_sha}
        self.protocol["amendment"] = {"path": amendment_path, "sha256": amendment_sha}
        self.protocol["input_bindings"].update({parent_path: parent_sha, outcome_path: outcome_sha, amendment_path: amendment_sha})
        self.protocol["methods_profile"]["encoding_amendment"] = {"scope": "encoding_only", "amendment_sha256": amendment_sha}

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return runner.file_sha(path)

    def rewrite_outcome(self):
        item = self.protocol["parent_result"]
        item["sha256"] = self.write(item["path"], json.dumps(self.outcome))
        self.protocol["input_bindings"][item["path"]] = item["sha256"]

    def test_encoding_only_amendment_with_retained_failure_is_valid(self):
        got = runner.validate_parent_lineage(self.protocol, self.root)
        self.assertEqual(got["parent_result_sha256"], self.protocol["parent_result"]["sha256"])

    def test_relabelling_failed_parent_successful_is_rejected(self):
        self.outcome["status"] = "complete"
        self.rewrite_outcome()
        with self.assertRaisesRegex(ValueError, "technical-failure"):
            runner.validate_parent_lineage(self.protocol, self.root)

    def test_incomplete_parent_denominator_is_rejected(self):
        self.outcome["records"].pop()
        self.rewrite_outcome()
        with self.assertRaisesRegex(ValueError, "technical-failure"):
            runner.validate_parent_lineage(self.protocol, self.root)

    def test_parent_result_cannot_point_to_another_protocol(self):
        self.outcome["protocol_sha256"] = "different parent"
        self.rewrite_outcome()
        with self.assertRaisesRegex(ValueError, "technical-failure"):
            runner.validate_parent_lineage(self.protocol, self.root)

    def test_source_reordering_and_count_tuning_are_rejected(self):
        for change in ("order", "count"):
            with self.subTest(change=change):
                changed = deepcopy(self.protocol)
                if change == "order":
                    changed["sources"][0], changed["sources"][1] = changed["sources"][1], changed["sources"][0]
                else:
                    changed["sources"][0]["expected_nonfraction_count"] -= 1
                with self.assertRaisesRegex(ValueError, "frozen field: sources"):
                    runner.validate_parent_lineage(changed, self.root)

    def test_reference_runtime_upgrade_cannot_hide_in_encoding_amendment(self):
        self.protocol["reference_runtime"]["version"] = "new runtime"
        with self.assertRaisesRegex(ValueError, "reference_runtime"):
            runner.validate_parent_lineage(self.protocol, self.root)

    def test_old_code_cannot_be_repaired_in_place(self):
        self.write("src/temporal_state/typed_reader_v15.py", "silently corrected old code")
        with self.assertRaisesRegex(ValueError, "historical code changed"):
            runner.validate_parent_lineage(self.protocol, self.root)

    def test_other_method_changes_are_rejected(self):
        self.protocol["methods_profile"]["unsupported"] = "drop difficult examples"
        with self.assertRaisesRegex(ValueError, "method changes exceed"):
            runner.validate_parent_lineage(self.protocol, self.root)

    def test_amendment_requires_explicit_input_binding(self):
        del self.protocol["input_bindings"][self.protocol["amendment"]["path"]]
        with self.assertRaisesRegex(ValueError, "must be an input binding"):
            runner.validate_parent_lineage(self.protocol, self.root)

    def test_parent_result_bytes_cannot_change_after_binding(self):
        self.write(self.protocol["parent_result"]["path"], "altered result")
        with self.assertRaisesRegex(ValueError, "parent/amendment digest mismatch"):
            runner.validate_parent_lineage(self.protocol, self.root)


if __name__ == "__main__":
    unittest.main()

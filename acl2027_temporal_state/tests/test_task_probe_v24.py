"""Authored checks for exact source interval scoring, not semantic validity."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/evaluate_task_probe_v24.py"
SPEC = importlib.util.spec_from_file_location("task_probe_eval", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceIntervalTests(unittest.TestCase):
    def test_union_requires_no_missing_gap(self):
        a = {"source_file": "A", "byte_start": 4, "byte_stop": 20}
        self.assertTrue(MODULE.covered(a, {"A": [[12, 25], [0, 12]]}))
        self.assertFalse(MODULE.covered(a, {"A": [[0, 11], [12, 25]]}))
        self.assertFalse(MODULE.covered(a, {"B": [[0, 25]]}))

    def test_alternative_witness_counts_and_no_semantic_promotion(self):
        a = lambda start: {"source_file": "A", "byte_start": start, "byte_stop": start + 2}
        ref = {"item_id": "q", "history_id": "h", "family": "f", "status": "supported",
               "claims": [{"claim_id": "c", "witness_sets": [[a(2), a(20)], [a(40)]]}]}
        run = {"item_id": "q", "policy": "baseline", "budget_words": 50,
               "rendered_words": 5, "rendered_source_intervals": {"A": [[40, 42]]}}
        result = MODULE.evaluate({"items": [{"item_id": "q"}]}, [ref], {"records": [run]})
        self.assertEqual(result["records"][0]["covered_claim_ids"], ["c"])
        self.assertIsNone(result["semantic_premise_recall"])
        self.assertFalse(result["automatic_coverage_authorized"])

    def test_entire_missing_policy_budget_is_rejected(self):
        protocol = {"items": [{"item_id": "q"}]}
        ref = {"item_id": "q", "history_id": "h", "family": "f", "status": "unresolved", "claims": []}
        run = {"item_id": "q", "policy": "p", "budget_words": 50,
               "rendered_words": 0, "rendered_source_intervals": {}}
        with self.assertRaisesRegex(ValueError, "full factorial"):
            MODULE.evaluate(protocol, [ref], {"records": [run]}, ["p", "q"], [50])

    def test_unresolved_reference_never_counts_as_complete(self):
        ref = {"item_id": "q", "history_id": "h", "family": "f", "status": "unresolved", "claims": []}
        run = {"item_id": "q", "policy": "p", "budget_words": 50,
               "rendered_words": 0, "rendered_source_intervals": {}}
        result = MODULE.evaluate({"items": [{"item_id": "q"}]}, [ref], {"records": [run]}, ["p"], [50])
        self.assertFalse(result["records"][0]["source_supported_complete_question"])
        self.assertFalse(result["records"][0]["all_recorded_witnesses_retrieved"])

    def test_selection_accounting_rejects_unrendered_coverage_and_cost(self):
        catalog = {"b": {"source_version": "A", "source_sha256": "h", "render_words": 5,
                         "rendered_source_intervals": [[10, 20]]}}
        row = {"selected_block_ids": ["b"], "rendered_words": 5,
               "rendered_source_intervals": {"A": [[10, 20]]}}
        MODULE.validate_selection(row, catalog, {"A": "h"})
        row["rendered_source_intervals"] = {"A": [[0, 20]]}
        with self.assertRaisesRegex(ValueError, "coverage"):
            MODULE.validate_selection(row, catalog, {"A": "h"})
        row["rendered_words"] = 0
        with self.assertRaisesRegex(ValueError, "cost"):
            MODULE.validate_selection(row, catalog, {"A": "h"})


if __name__ == "__main__":
    unittest.main()

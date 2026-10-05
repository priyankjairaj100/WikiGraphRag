"""Authored regression checks, never reader competence or model evaluations."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_reader_gate_v23 import exact_decimal, render_request, make_pair, eligible_singletons, digest
from analyze_reader_gate_v23 import score_item, parse_prediction, score_attempt, validate_preflight


def fact(index, amount="7", period="2024-12-31", status="normalized"):
    return {"source_sha256": "a" * 64, "source_version": "source-A", "fact_ordinal": index,
            "reported_aspects": {"concept": "{urn:test}Amount", "entity": {"scheme": "test", "identifier": "I"},
                                 "period": {"instant": period}, "unit": {"numerator": ["{urn:iso}USD"], "denominator": []}, "dimensions": []},
            "binding_status": "reported_aspects_resolved", "visibility": "rendering_unverified",
            "normalized_value": amount, "status": status,
            "anchor": {"byte_start": index * 10, "byte_stop": index * 10 + 5, "span_sha256": "b" * 64},
            "evidence_anchors": []}


class ReaderGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((ROOT / "data/reader_gate_v23/protocol.json").read_text())

    def pair(self, two=False):
        facts = [fact(i, str(i + 7), f"20{i:02}-12-31") for i in range(10)]
        return make_pair(facts[:2] if two else facts[:1], facts, "I", self.protocol)

    def test_signed_zero_and_large_exact_arithmetic(self):
        self.assertEqual(exact_decimal("subtract", ["-100", "100"]), "-200")
        self.assertEqual(exact_decimal("subtract", ["0", "-1"]), "1")
        self.assertEqual(exact_decimal("subtract", ["1000000000000000000000000000000.001", "1000000000000000000000000000000"]), "0.001")
        self.assertEqual(exact_decimal("identity", ["-0.00"]), "0")
        for bad in ("NaN", "Infinity", "1e10001"):
            with self.assertRaises(ValueError):
                exact_decimal("identity", [bad])

    def test_conflicting_binding_not_selected(self):
        valid, excluded = eligible_singletons({"facts": [fact(0, "-100"), fact(1, "100")]})
        self.assertEqual(valid, [])
        self.assertEqual(excluded, {"competing_values_same_binding": 1})
        valid, _ = eligible_singletons({"facts": [fact(0), fact(1, status="unresolved")]})
        self.assertEqual(valid, [])

    def test_request_does_not_contain_private_labels(self):
        for item in self.pair():
            request = render_request(item, self.protocol)
            text = json.dumps(request)
            for hidden in (item["item_id"], item["family_id"], "reference", "evidence_condition", "target_selectors"):
                self.assertNotIn(hidden, text)
            payload = json.loads(request["messages"][1]["content"])
            self.assertEqual(set(payload), {"question", "evidence"})
            self.assertEqual(len(payload["evidence"]), 5)

    def test_incomplete_does_not_show_missing_target(self):
        complete, missing = self.pair(True)
        absent_scope = missing["required_scopes"][-1]
        for e in missing["evidence"]:
            shown_scope = {k: v for k, v in e["record"].items() if k != "normalized_value"}
            self.assertNotEqual(shown_scope, absent_scope)
        self.assertEqual(complete["question"], missing["question"])
        self.assertEqual(len(complete["evidence"]), len(missing["evidence"]))

    def test_joint_scope_value_and_citations_all_required(self):
        item, _ = self.pair(True)
        self.assertTrue(score_item(item, json.dumps(item["reference"]))["joint_correct"])
        bad = copy.deepcopy(item["reference"])
        bad["operand_evidence_ids"].reverse()
        self.assertFalse(score_item(item, json.dumps(bad))["joint_correct"])
        bad = copy.deepcopy(item["reference"])
        bad["unit"] = {"numerator": ["EUR"], "denominator": []}
        self.assertFalse(score_item(item, json.dumps(bad))["joint_correct"])
        bad = copy.deepcopy(item["reference"])
        bad["evidence_ids"] = ["NONEXISTENT"]
        self.assertFalse(score_item(item, json.dumps(bad))["valid"])

    def test_strict_json_rejects_duplicate_keys_answerful_abstention(self):
        with self.assertRaises(ValueError):
            parse_prediction('{"status":"abstain","status":"answer"}')
        _, missing = self.pair()
        bad = copy.deepcopy(missing["reference"])
        bad["value"] = "123"
        self.assertFalse(score_item(missing, json.dumps(bad))["valid"])
        self.assertTrue(score_item(missing, json.dumps(missing["reference"]))["joint_correct"])

    def test_perfect_small_fixture_cannot_open_gate(self):
        items = self.pair()
        predictions = [{"item_id": i["item_id"], "request_sha256": i["request_sha256"], "raw_response": json.dumps(i["reference"])} for i in items]
        result = score_attempt(items, predictions, self.protocol)
        self.assertFalse(result["control_thresholds_met"])
        self.assertFalse(result["natural_reader_gate_open"])
        self.assertFalse(result["comparison_authorized"])

    def test_duplicate_missing_predictions_fail(self):
        items = self.pair()
        p = {"item_id": items[0]["item_id"], "request_sha256": items[0]["request_sha256"], "raw_response": json.dumps(items[0]["reference"])}
        with self.assertRaises(ValueError):
            score_attempt(items, [p, p], self.protocol)
        with self.assertRaises(ValueError):
            score_attempt(items, [p], self.protocol)

    def test_pending_preflight_rejected(self):
        items = self.pair()
        requests = [render_request(i, self.protocol) for i in items]
        manifest = {"protocol_sha256": "x"}
        preflight = {"protocol_sha256": "x", "items_content_sha256": digest(items), "requests_content_sha256": digest(requests),
                     "model": {k: self.protocol["model"][k] for k in ("repository", "revision", "dtype", "quantization")},
                     "runtime_artifact_sha256": "PENDING"}
        with self.assertRaisesRegex(ValueError, "runtime_artifact"):
            validate_preflight(preflight, self.protocol, manifest, items, requests)


if __name__ == "__main__":
    unittest.main()

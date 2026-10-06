"""Boundary tests for joint, grounded interpretation scoring."""
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/score_interpretations_v26.py"
SPEC = importlib.util.spec_from_file_location("score_v26", PATH)
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


def known(value, evidence="Q"):
    return {"state": "known", "accepted_values": [value], "evidence_sets": [[evidence]]}


def candidate(**updates):
    value = {"id": "c1", **{f: "NA" for f in score.FIELDS}, "evidence_ids": ["Q"]}
    value.update(updates)
    return value


def response(c, status="resolved"):
    return {"status": status, "selected": c["id"] if status == "resolved" else None,
            "candidates": [c], "changes": []}


class ScoringBoundaries(unittest.TestCase):
    def test_no_cross_class_field_merging(self):
        ref = {"status": "resolved", "classes": [
            {"class_id": "a", "fields": {"entity": known("A"), "time": known("2020")}},
            {"class_id": "b", "fields": {"entity": known("B"), "time": known("2021")}}]}
        result = score.score_parsed(response(candidate(entity="A", time="2021")), ref, ["entity", "time"])
        self.assertFalse(result["strict_correct"])
        self.assertIsNone(result["asserted_interpretation_error"])

    def test_unknown_not_applicable_and_missing_witness(self):
        c = candidate(time="NA", entity="A")
        self.assertFalse(score.field_match(c, "time", {"state": "unknown"}))
        self.assertTrue(score.field_match(c, "time", {"state": "not_applicable"}))
        self.assertFalse(score.field_match(c, "entity", known("A", "s1")))

    def test_duplicate_keys_and_invented_sources_rejected(self):
        with self.assertRaises(ValueError):
            score.parse_response('{"status":"resolved","status":"insufficient"}', ["Q"], "I0")
        with self.assertRaisesRegex(ValueError, "unknown_evidence"):
            score.parse_response(json.dumps(response(candidate(entity="A", evidence_ids=["hidden"]))), ["Q"], "I0")

    def test_ambiguity_requires_every_observed_class(self):
        ref = {"status": "ambiguous", "classes": [
            {"class_id": "a", "fields": {"entity": known("A")}},
            {"class_id": "b", "fields": {"entity": known("B")}}]}
        value = response(candidate(entity="A"), "ambiguous")
        self.assertFalse(score.score_parsed(value, ref, ["entity"])["strict_correct"])
        value["candidates"].append(candidate(id="c2", entity="B"))
        self.assertTrue(score.score_parsed(value, ref, ["entity"])["strict_correct"])

    def test_truncated_output_never_scores_as_success(self):
        result = score.score_raw("{}", {"status": "resolved"}, [], ["Q"], "I0", transport_status="output_limit")
        self.assertFalse(result["strict_correct"])
        self.assertEqual(result["execution_failure"], "output_limit")

    def test_normalization_preserves_meaningful_symbols(self):
        self.assertEqual(score.normalize("  Ａ  B\n"), "a b")
        self.assertNotEqual(score.normalize("-2 before 2020"), score.normalize("2 after 2020"))

    def test_ledger_defect_does_not_penalize_correct_interpretation(self):
        ref = {"status": "resolved", "classes": [{"class_id": "a", "fields": {"entity": known("A")}}]}
        draft = response(candidate(entity="A"))
        result = score.score_raw(json.dumps(draft), ref, ["entity"], ["Q"], "R1", previous=draft,
                                 previous_evidence_ids=["Q"])
        self.assertTrue(result["strict_correct"])
        self.assertFalse(result["ledger_valid"])
        draft["changes"] = [{"old": None, "new": "c1", "action": "add", "evidence_ids": ["Q"]}]
        result = score.score_raw(json.dumps(draft), ref, ["entity"], ["Q"], "R1", previous={"bad": True},
                                 previous_evidence_ids=["Q"])
        self.assertTrue(result["strict_correct"])
        self.assertTrue(result["ledger_valid"])

    def test_unscorable_reference_never_enters_denominator(self):
        for raw in ("{}", "invalid"):
            result = score.score_raw(raw, {"status": "unscorable"}, [], ["Q"], "I0")
            self.assertIsNone(result["strict_correct"])

    def test_retained_candidate_cannot_be_retired_in_ledger(self):
        draft = response(candidate(entity="A"))
        value = response(candidate(entity="A"))
        value["changes"] = [{"old": "c1", "new": None, "action": "retire", "evidence_ids": ["Q"]}]
        with self.assertRaisesRegex(ValueError, "retire_links"):
            score.validate_ledger(value, ["Q"], "R1", draft)


if __name__ == "__main__":
    unittest.main()

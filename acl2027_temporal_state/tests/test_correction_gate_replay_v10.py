"""Small synthetic interface contracts; these are not natural-history results."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "correction_gate_replay_v10", ROOT / "scripts/replay_correction_gate_v10.py")
replay_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay_module)


class CorrectionGateReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.sentences = [
            "Acme reports original input 10.",
            "Acme corrects input to 12; replaces original input 10.",
            "Acme reports dependent output 20 using only original input 10.",
            "Acme corrects dependent output to 24; replaces dependent output 20.",
            "Acme retains independent control 7.",
        ]
        self.source = "\n".join(self.sentences)
        self.source_path = self.directory / "synthetic.txt"
        self.source_path.write_bytes(self.source.encode("utf-8"))

        def span(quote):
            start = self.source.index(quote)
            return {"start": start, "end": start + len(quote), "quote": quote}

        self.annotation = {
            "schema_version": "correction_gate_annotation_v0.10",
            "history_id": "synthetic_interface_contract",
            "source_representation": "web_tool_noncontiguous_excerpt_projection",
            "operational_cutoff": "2026-10-03",
            "sources": [{"source_id": "s", "local_path": "synthetic.txt",
                         "sha256": sha256(self.source.encode("utf-8")).hexdigest(),
                         "capture_date": "2026-10-03"}],
            "claims": [
                {"claim_id": cid, "source_id": "s", "entity": "Acme",
                 "relation": relation, "value": value, "scope": "",
                 "modality": "reported_actual", "evidence_spans": [span(sentence)]}
                for cid, relation, value, sentence in zip(
                    ["old", "new", "downstream_old", "downstream_new", "control"],
                    ["input", "input", "output", "output", "control"],
                    ["10", "12", "20", "24", "7"], self.sentences)],
            "authorities": [{"source_id": "s", "authority_id": "Acme",
                             "evidence_spans": [span("Acme")]}],
            "corrections": [
                {"replacement_claim_id": "new", "target_claim_id": "old",
                 "reference_span": span(self.sentences[1])},
                {"replacement_claim_id": "downstream_new", "target_claim_id": "downstream_old",
                 "reference_span": span(self.sentences[3])}],
            "dependencies": [{"dependent_claim_id": "downstream_old", "prerequisite_claim_id": "old",
                              "evidence_spans": [span(self.sentences[2])]}],
            "unaffected_claim_ids": ["control"],
        }
        self.annotation_path = self.directory / "annotation.json"

    def run_replay(self, annotation=None):
        self.annotation_path.write_text(json.dumps(annotation or self.annotation), encoding="utf-8")
        return replay_module.replay(self.annotation_path, self.directory)[0]

    def test_all_direct_corrections_retained_without_apparent_dependency_gain(self):
        result = self.run_replay()
        arms = {row["policy"]: row for row in result["policies"]}
        self.assertEqual(set(arms["no_withdrawal"]["active_claim_ids"]),
                         {"old", "new", "downstream_old", "downstream_new", "control"})
        for name in ("direct_withdrawal", "dependency_withdrawal"):
            ledger = arms[name]["ledger"]
            self.assertEqual(set(ledger["directly_withdrawn_claim_ids"]), {"old", "downstream_old"})
            self.assertEqual(len([lid for lid in ledger["selected_link_ids"].values() if lid]), 2)
            self.assertEqual(set(arms[name]["active_claim_ids"]), {"new", "downstream_new", "control"})
        self.assertEqual(result["dependency_additional_inactive_claim_ids"], [])
        self.assertEqual(result["qa_predictions"], 0)
        self.assertFalse(result["decoder_run"])

    def test_projection_preserves_representation_and_bounds_without_quotes(self):
        result = self.run_replay()
        self.assertEqual(result["source_representation"], "web_tool_noncontiguous_excerpt_projection")
        encoded = json.dumps(result)
        self.assertNotIn('"quote":', encoded)
        for sentence in self.sentences:
            self.assertNotIn(sentence, encoded)
        for arm in result["policies"]:
            self.assertTrue(arm["unknown_endpoints_preserved"])
            for claim in arm["ledger"]["selected_claims"]:
                self.assertEqual(claim["start"], {"lower": None, "upper": None})
                self.assertEqual(claim["end"], {"lower": None, "upper": None})
                self.assertEqual(claim["state_observed_at"], [])

    def test_source_hash_mismatch_rejected(self):
        self.source_path.write_bytes((self.source + "\nchanged").encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.run_replay()

    def test_bad_span_rejected(self):
        annotation = deepcopy(self.annotation)
        annotation["claims"][0]["evidence_spans"][0]["quote"] = "unsupported quotation"
        with self.assertRaisesRegex(ValueError, "exact source slice"):
            self.run_replay(annotation)

    def test_invented_event_bounds_rejected(self):
        annotation = deepcopy(self.annotation)
        annotation["claims"][0]["start"] = {"lower": "2026-10-03", "upper": "2026-10-03"}
        with self.assertRaisesRegex(ValueError, "Unexpected or missing fields in claim"):
            self.run_replay(annotation)

    def test_missing_correction_target_rejected(self):
        annotation = deepcopy(self.annotation)
        annotation["corrections"][0]["target_claim_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown claim"):
            self.run_replay(annotation)

    def test_duplicate_outgoing_correction_rejected_not_overwritten(self):
        annotation = deepcopy(self.annotation)
        duplicate = deepcopy(annotation["corrections"][0])
        duplicate["target_claim_id"] = "control"
        annotation["corrections"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "one selected correction per replacement"):
            self.run_replay(annotation)

    def test_falsely_declared_unaffected_target_rejected(self):
        annotation = deepcopy(self.annotation)
        annotation["unaffected_claim_ids"] = ["old"]
        with self.assertRaisesRegex(ValueError, "unaffected retention control was withdrawn"):
            self.run_replay(annotation)


if __name__ == "__main__":
    unittest.main()

"""Request isolation, immutable inputs, and exact draft preservation."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("requests_v26", ROOT / "scripts/prepare_interpretation_requests_v26.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class RequestTests(unittest.TestCase):
    def fixtures(self, folder, count=1):
        config = {"prompts": {"system": "Read evidence.", "initial": "Extract.",
                             "reextract": "Extract again.", "revision": "Revise."},
                  "output_schema": {"type": "object"}, "maximum_questions": 60,
                  "stage_a": {"labels": ["eligible_resolved", "ineligible_no_temporal_target"]}}
        module.write(folder / "config.json", config)
        module.write(folder / "runtime.json", {})
        (folder / "references.txt").write_text("SECRET REFERENCE CLASSES; deliberately not valid JSON")
        packets = []
        for i in range(count):
            text = "E1: An authored source."
            pack = {"text": text, "sha256": module.text_hash(text), "evidence_ids": ["E1"],
                    "units": [{"evidence_id": "E1", "source_id": "source1", "byte_start": 0,
                               "byte_stop": 10, "span_sha256": "authored-control"}], "token_count": 8}
            packets.append({"item_id": "v26-%03d" % i, "query": "Which event?",
                            "query_sha256": module.text_hash("Which event?"), "c0": pack, "c1": pack})
        module.write_rows(folder / "packets.jsonl", packets)
        module.write_rows(folder / "admissions.jsonl", [{"item_id": p["item_id"], "eligibility": "eligible_resolved"}
                                                      for p in packets])
        args = argparse.Namespace(packets=folder / "packets.jsonl", admissions=folder / "admissions.jsonl",
                   config=folder / "config.json", runtime_config=folder / "runtime.json",
                   references=[folder / "references.txt"], extra_freeze=[], constrained_decoding=True,
                   output=folder / "initial", public_receipt=folder / "public_initial.json")
        module.first_stage(args)
        return args

    def make_i0(self, folder, first_args, content, missing=False):
        initial = first_args.output
        frozen = module.check_freeze(initial)
        runtime = folder / "i0_run"
        runtime.mkdir()
        module.write(runtime / "freeze.json", {"requests_sha256": frozen["I0_requests_sha256"],
                     "config_sha256": frozen["input_sha256"]["runtime_config"],
                     "script_sha256": frozen["input_sha256"]["runtime_script"]})
        for row in module.read_rows(initial / "I0_requests.jsonl"):
            item = runtime / row["id"]
            item.mkdir()
            module.write(item / "input.json", row)
            result = {"id": row["id"], "status": "technical_failure" if missing else "completed"}
            if not missing:
                module.write(item / "response.raw.json", {"content": content})
                result["raw_response_sha256"] = module.digest(item / "response.raw.json")
            module.write(item / "result.json", result)
        return argparse.Namespace(first_stage=initial, freeze_sha256=module.digest(initial / "freeze.json"),
                   i0_run=runtime, output=folder / "expanded", public_receipt=folder / "public_expanded.json")

    def test_reference_classes_never_enter_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            args = self.fixtures(folder)
            payload = (args.output / "I0_requests.jsonl").read_text()
            public = Path(args.public_receipt).read_text()
            self.assertNotIn("SECRET REFERENCE", payload + public)
            self.assertIn("An authored source", payload)

    def test_reference_change_blocks_stage_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            args = self.fixtures(folder)
            (folder / "references.txt").write_text("amended reference")
            with self.assertRaisesRegex(ValueError, "frozen_input_changed_reference"):
                module.check_freeze(args.output)

    def test_malformed_draft_is_preserved_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self.fixtures(folder)
            raw = ' {"broken":\n\n PREVIOUS fake\n'
            args = self.make_i0(folder, first, raw)
            module.second_stage(args)
            rows = module.read_rows(args.output / "expanded_requests.jsonl")
            for row in rows:
                content = row["messages"][1]["content"]
                if row["id"].endswith((".B1", ".R1")):
                    self.assertTrue(content.endswith("PREVIOUS\n" + raw))
                else:
                    self.assertNotIn(raw, content)
                self.assertIn("output_schema", row)

    def test_missing_draft_blocks_only_dependent_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self.fixtures(folder)
            args = self.make_i0(folder, first, None, missing=True)
            module.second_stage(args)
            rows = module.read_rows(args.output / "expanded_requests.jsonl")
            self.assertEqual([row["id"] for row in rows], ["v26-000.F1"])
            receipt = json.loads(Path(args.public_receipt).read_text())
            self.assertEqual(sum(r["status"] == "blocked_missing_I0_response" for r in receipt["accounting"]), 2)

    def test_six_items_balance_all_arm_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            args = self.fixtures(folder, count=6)
            frozen = module.check_freeze(args.output)
            orders = list(frozen["second_stage_order"].values())
            self.assertEqual(len({tuple(row) for row in orders}), 6)
            for position in range(3):
                for arm in ["B1", "F1", "R1"]:
                    self.assertEqual(sum(row[position] == arm for row in orders), 2)

    def test_changed_i0_response_blocks_stage_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = self.fixtures(folder)
            args = self.make_i0(folder, first, "{}")
            (args.i0_run / "v26-000.I0/response.raw.json").write_text('{"content":"changed"}')
            with self.assertRaisesRegex(ValueError, "I0_response_digest_mismatch"):
                module.second_stage(args)


if __name__ == "__main__":
    unittest.main()

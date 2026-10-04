"""Offline authored driver checks: budgets, references, matched views and guards."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import native_binding_reader_v16 as native


def prompt(count=12):
    text, ids = "authored prompt", list(range(count))
    return {"rendered_prompt": text, "input_token_ids": ids, "input_tokens": count,
            "input_token_ids_sha256": native.base.canonical_hash(ids),
            "rendered_prompt_sha256": hashlib.sha256(text.encode()).hexdigest()}


class NativeBindingReaderTests(unittest.TestCase):
    def setUp(self):
        self.c = native.config(ROOT / "configs/binding_reader_v16.json")
        self.controls = native.load(ROOT / "data/reader_binding_v16/interface_controls_v16.json")
        self.items = native.authored_requests(self.controls)

    def test_config_binds_completed_exact_acquisition_and_relocation(self):
        self.assertEqual(self.c["model"]["sha256"], "40d0f32cd3030b04f0784139a589fb63e876cfbf8667d56311b79783c74fd149")
        self.assertEqual(self.c["maximum_context_tokens"], 8192)
        self.assertEqual(self.c["maximum_generated_tokens"], 768)

    def test_wrong_asset_metadata_rejected_without_model_hash_or_load(self):
        changed = deepcopy(self.c)
        changed["model"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory(dir="/dev/shm") as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "wrong_asset_binding"):
                native.config(path)

    def test_changed_memory_or_decode_caps_are_rejected(self):
        for field, value in [("total_memory_stop_bytes", 8589934592), ("maximum_generated_tokens", 1024),
                             ("enable_thinking", True), ("seed", 7), ("load_mode", "none")]:
            changed = deepcopy(self.c)
            changed[field] = value
            with tempfile.TemporaryDirectory(dir="/dev/shm") as folder:
                path = Path(folder) / "config.json"
                path.write_text(json.dumps(changed))
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "frozen_caps_changed"):
                    native.config(path)

    def test_native_full_prompt_budget_never_truncates(self):
        native.validate_prompt(prompt(6144), self.c)
        with self.assertRaisesRegex(ValueError, "input_over_budget"):
            native.validate_prompt(prompt(6145), self.c)

    def test_native_context_reserves_full_output(self):
        changed = deepcopy(self.c)
        changed["maximum_context_tokens"] = 6500
        with self.assertRaisesRegex(ValueError, "input_over_budget"):
            native.validate_prompt(prompt(6144), changed)

    def test_native_token_boolean_or_count_tamper_rejected(self):
        changed = prompt()
        changed["input_token_ids"][0] = True
        with self.assertRaisesRegex(ValueError, "token_array"):
            native.validate_prompt(changed, self.c)

    def test_native_response_rejects_output_overrun_cache_and_token_deficit(self):
        p = prompt()
        settings = {k: v for k, v in native.PARAMETERS.items() if k not in ("temperature", "cache_prompt", "return_tokens")}
        settings.update(generation_prompt="", lora=[], backend_sampling=False, temperature=0.0)
        response = {"prompt": p["rendered_prompt"], "model": self.c["model"]["path"], "truncated": False,
                    "tokens_evaluated": p["input_tokens"], "tokens_predicted": 3,
                    "timings": {"cache_n": 0, "prompt_n": p["input_tokens"], "predicted_n": 3},
                    "stop": True, "stop_type": "eos", "content": "{}", "tokens": [5, 6, 7],
                    "generation_settings": settings}
        native.validate_response(p, response, self.c)
        changed = deepcopy(response)
        changed["tokens_predicted"] = 769
        with self.assertRaisesRegex(ValueError, "output_over_budget"):
            native.validate_response(p, changed, self.c)
        changed = deepcopy(response)
        changed["tokens"] = []
        with self.assertRaisesRegex(ValueError, "token_list_count"):
            native.validate_response(p, changed, self.c)
        changed = deepcopy(response)
        changed["timings"]["cache_n"] = 1
        with self.assertRaisesRegex(ValueError, "cold_accounting"):
            native.validate_response(p, changed, self.c)
        changed = prompt()
        changed["input_tokens"] -= 1
        with self.assertRaisesRegex(ValueError, "token_count"):
            native.validate_prompt(changed, self.c)

    def test_eight_requests_preserve_same_question_and_compact_pack(self):
        self.assertEqual(len(self.items), 8)
        for i in range(0, 8, 2):
            left, right = [native.request_messages(x) for x in self.items[i:i+2]]
            self.assertEqual(left[0], right[0])
            self.assertEqual(left[1]["content"].split("\n\nOutput contract:")[0],
                             right[1]["content"].split("\n\nOutput contract:")[0])

    def test_reference_sentinel_never_enters_messages(self):
        changed = deepcopy(self.controls)
        changed["references"]["private_sentinel"] = "DO_NOT_SEND_REFERENCE_749185"
        messages = [native.request_messages(x) for x in native.authored_requests(changed)]
        self.assertNotIn("DO_NOT_SEND_REFERENCE_749185", json.dumps(messages))

    def test_extra_reference_field_in_request_is_rejected(self):
        item = deepcopy(self.items[0])
        item["reference"] = {"answer": "private"}
        with self.assertRaisesRegex(ValueError, "request_shape"):
            native.request_messages(item)

    def test_hidden_document_pointer_in_pack_is_rejected(self):
        item = deepcopy(self.items[0])
        item["pack"]["hidden_document"] = "/authored/never-open"
        with self.assertRaisesRegex(ValueError, "pack_shape"):
            native.request_messages(item)

    def test_all_authored_reference_outputs_pass_interface_gate(self):
        for item in self.items:
            reference = self.controls["references"][item["fixture_id"]]
            facts = {f["handle"]: f for f in item["pack"]["facts"]}
            claims = []
            for quantity in reference["expected_quantities"]:
                fact = facts[quantity["fact_handle"]]
                claims.append({"fact_handle": fact["handle"], "binding_witnesses": {
                    a: fact["bindings"][a]["witnesses"] for a in native.ASPECTS}})
            common = {"unknown": False, "clarify_aspects": reference["expected_clarify_aspects"]}
            if item["arm"] == "B1":
                output = common | {"decision": "answer" if reference["expected_action"] == "answered" else reference["expected_action"],
                                   "hypotheses": [{"claims": claims}] if claims else []}
            else:
                output = common | {"action": reference["expected_action"], "quantities": [
                    claim | {"value": q["value"]} for claim, q in zip(claims, reference["expected_quantities"])]}
            result, summary = native.assessment(item, json.dumps(output), reference)
            with self.subTest(item=item["request_id"]):
                self.assertTrue(summary["authored_control_correct"], result)

    def test_wrong_direct_value_fails_even_when_schema_valid(self):
        item = self.items[1]
        fact = item["pack"]["facts"][0]
        output = {"action": "answered", "unknown": False, "clarify_aspects": [], "quantities": [{
            "fact_handle": fact["handle"], "value": "999", "binding_witnesses": {
                a: fact["bindings"][a]["witnesses"] for a in native.ASPECTS}}]}
        _, summary = native.assessment(item, json.dumps(output), self.controls["references"][item["fixture_id"]])
        self.assertTrue(summary["schema_valid"])
        self.assertFalse(summary["exposed_support_valid"])
        self.assertFalse(summary["authored_control_correct"])

    def test_invalid_json_is_retained_not_repaired(self):
        for item in self.items[:2]:
            _, summary = native.assessment(item, '```json\n{"action":"answered"}\n```')
            self.assertEqual(summary["action"], "invalid_output")

    def test_total_and_active_memory_guards_are_both_enforced(self):
        for current, active, error in [(self.c["total_memory_stop_bytes"], 1, "total_memory_limit"),
                                       (1, self.c["active_memory_stop_bytes"], "active_memory_limit"),
                                       (None, 1, "telemetry")]:
            with patch.object(native.guard, "cgroup_current", return_value=current), \
                 patch.object(native.guard, "cgroup_resident", return_value=active), \
                 self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                native.memory_sample(self.c)

    def test_command_has_mmap_single_slot_and_no_context_shift(self):
        cmd = native.command(self.c)
        self.assertEqual(cmd[cmd.index("--load-mode") + 1], "mmap")
        self.assertEqual(cmd[cmd.index("--parallel") + 1], "1")
        self.assertIn("--no-context-shift", cmd)
        self.assertNotIn("--mlock", cmd)
        self.assertNotIn("--no-mmap", cmd)

    def natural_pair(self):
        pair = deepcopy(self.items[:2])
        for item in pair:
            item["case_id"] = item.pop("fixture_id")
        return pair

    def test_natural_requests_are_paired_and_bounded(self):
        pair = self.natural_pair()
        self.assertEqual(native.natural_requests(pair), pair)
        with self.assertRaisesRegex(ValueError, "natural_request_limit"):
            native.natural_requests(pair[:1])
        with self.assertRaisesRegex(ValueError, "natural_request_limit"):
            native.natural_requests(pair * 19)

    def test_mismatched_natural_evidence_fails_before_execution(self):
        pair = self.natural_pair()
        pair[1]["question"] += " changed"
        with self.assertRaisesRegex(ValueError, "unmatched_arm_evidence"):
            native.natural_requests(pair)

    def test_verify_rejects_missing_assessment_instead_of_zip_truncation(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as folder:
            path = Path(folder)
            native.write(path / "native_prompts.json", [{}, {}])
            frozen = {"maximum_completions": 2}
            receipt = {"status": "completed", "completion_calls_started": 2,
                       "completion_responses": 2, "assessments": [{}]}
            with patch.object(native, "check_frozen", return_value=(frozen, [{}, {}], [{}, {}], receipt)), \
                 self.assertRaisesRegex(ValueError, "verify_row_count_mismatch"):
                native.verify(path)

    def test_model_hash_uses_streaming_cache_discard(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as folder:
            path = Path(folder) / "fake.gguf"
            path.write_bytes(b"authored model bytes")
            with patch.object(native.os, "posix_fadvise") as advise:
                self.assertEqual(native.model_digest(path), hashlib.sha256(path.read_bytes()).hexdigest())
            advise.assert_called()


if __name__ == "__main__":
    unittest.main()

"""Checks for input isolation and interruption recovery, without model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/run_interpretation_v26.py"
SPEC = importlib.util.spec_from_file_location("runtime_v26", PATH)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def row(self):
        return {"id": "item01", "messages": [{"role": "user", "content": "Read this source."}]}

    def test_reference_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.jsonl"
            path.write_text(json.dumps(dict(self.row(), gold_answer="hidden")) + "\n")
            with self.assertRaisesRegex(ValueError, "unexpected_input_keys"):
                runtime.load_requests(path)

    def test_duplicate_identifiers_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.jsonl"
            path.write_text((json.dumps(self.row()) + "\n") * 2)
            with self.assertRaisesRegex(ValueError, "duplicate_id"):
                runtime.load_requests(path)

    def test_interrupted_call_is_never_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            runtime.write(folder / "call_started.json", {"started_at_unix": 1})
            result = runtime.recover_record(folder, self.row())
            self.assertEqual(result["status"], "interrupted_no_retry")
            self.assertEqual(runtime.recover_record(folder, self.row()), result)

    def test_raw_response_recovers_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            runtime.write(folder / "native_prompt.json", {"tokens": [1, 2]})
            runtime.write(folder / "response.raw.json", {"content": '{"status":"unknown"}',
                          "stop_type": "eos", "tokens_predicted": 8, "truncated": False})
            result = runtime.recover_record(folder, self.row())
            self.assertEqual(result["parsed_json"], {"status": "unknown"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["input_tokens"], 2)

    def test_changed_raw_response_blocks_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            response = {"content": "{}", "stop_type": "limit", "tokens_predicted": 768}
            runtime.write(folder / "response.raw.json", response)
            result = runtime.record_completion(folder, self.row(), response, 2)
            self.assertEqual(result["status"], "output_limit")
            (folder / "response.raw.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "resume_response_changed"):
                runtime.recover_record(folder, self.row())


if __name__ == "__main__":
    unittest.main()

"""Focused authored-only RGBA consumer checks; native execution is always mocked."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import render_source_blocks_lo_v17_2 as renderer


def authored_item(style="color:rgba(0,0,0,1)", count=1):
    fragment = ("<div>" + "".join(
        '<p style="' + style + '">Authored unchanged text</p>'
        for _ in range(count)) + "</div>").encode()
    raw = (b'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>'
           + fragment + b'</body></html>')
    return {"block_id": "authored", "html": raw, "fragment": fragment}


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        renderer.RUN_CLEANUP_FAILURE = None

    def test_exact_originals_derived_and_ledger_are_retained_before_mocked_stop(self):
        item = authored_item()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                renderer, "run_command", return_value={"status": "authored_mock_stop"}) as run:
            attempt = Path(directory)
            folder = attempt / "block"
            result = renderer.render_block(item, folder, attempt, 1)
            self.assertEqual(result["status"], "authored_mock_stop")
            self.assertEqual(run.call_count, 1)
            self.assertEqual((folder / "source-fragment.xml").read_bytes(), item["fragment"])
            self.assertEqual((folder / "source-assembly.html").read_bytes(), item["html"])
            derived = (folder / "compatibility-assembly.html").read_bytes()
            self.assertEqual(derived, item["html"].replace(b"rgba(0,0,0,1)", b"rgba(0,0,0,100%)"))
            ledger = json.loads((folder / "compatibility-patches.json").read_text())
            self.assertEqual(ledger["patch_count"], 1)
            self.assertEqual(ledger["derived_assembly_sha256"], renderer.source_render.sha(derived))
            final = (folder / "source-block.html").read_bytes()
            self.assertEqual(final, derived.replace(b"<head>", b'<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>', 1))
            self.assertEqual(result["render_html_sha256"], renderer.source_render.sha(final))

    def test_transparent_foreground_refusal_keeps_originals_and_never_launches(self):
        item = authored_item("color:rgba(0,0,0,0)")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                renderer, "run_command", side_effect=AssertionError("Native execution forbidden")) as run:
            attempt = Path(directory)
            folder = attempt / "block"
            result = renderer.render_block(item, folder, attempt, 1)
            self.assertEqual(result["status"], "unrenderable_qualified_alpha_profile_refusal")
            run.assert_not_called()
            self.assertEqual((folder / "source-assembly.html").read_bytes(), item["html"])
            self.assertFalse((folder / "compatibility-assembly.html").exists())

    def test_additional_patch_ledger_bytes_are_bounded_before_derived_writes(self):
        item = authored_item(count=20)
        original_estimate = 3 * len(item["html"]) + len(item["fragment"]) + 256
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                renderer, "run_command", side_effect=AssertionError("Native execution forbidden")) as run, \
                mock.patch.object(renderer, "MAX_BYTES", original_estimate + 1), \
                mock.patch.object(renderer, "RECEIPT_RESERVE", 0):
            attempt = Path(directory)
            folder = attempt / "block"
            result = renderer.render_block(item, folder, attempt, 1)
            self.assertIn("output_cap", result["status"])
            run.assert_not_called()
            self.assertEqual((folder / "source-assembly.html").read_bytes(), item["html"])
            self.assertFalse((folder / "compatibility-assembly.html").exists())
            self.assertFalse((folder / "compatibility-patches.json").exists())
            self.assertLess(renderer.byte_size(attempt), renderer.MAX_BYTES)


if __name__ == "__main__":
    unittest.main()

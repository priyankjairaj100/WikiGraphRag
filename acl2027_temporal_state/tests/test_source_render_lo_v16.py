"""Authored LO probe bounds; these checks do not invoke a document conversion."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'scripts/probe_source_render_lo_v16.py'
SPEC = importlib.util.spec_from_file_location('probe_source_render_lo_v16', PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_over_cap_refuses_process_before_launch(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as folder:
            with patch.object(probe, 'memory_bytes', return_value=probe.MEMORY_CAP), \
                    patch.object(probe.subprocess, 'Popen') as popen:
                record = probe.monitored(['never-execute'], Path(folder))
            self.assertFalse(record['process_started'])
            self.assertEqual('blocked_before_launch_memory_cap', record['status'])
            popen.assert_not_called()

    def test_public_repository_destination_refused(self):
        with self.assertRaisesRegex(ValueError, 'outside_git'):
            probe.run_probe(probe.ROOT / 'never-create-authored-probe')

    def test_fixture_preserves_merged_headers_and_caption(self):
        module = probe.load_module(probe.ROOT / 'tests/test_source_render_v16.py', 'authored_lo_test_fixture')
        result = probe.source_render.prepare_blocks(module.FIXTURE, module.selection())[0]
        self.assertIn(b'rowspan="2"', result['html'])
        self.assertIn(b'colspan="2"', result['html'])
        self.assertIn(b'<caption>Authored source table</caption>', result['html'])
        self.assertIn(b'<span>1,</span><span>234</span>', result['html'])
        self.assertNotIn(b'https://invalid.example', result['html'])


if __name__ == '__main__':
    unittest.main()

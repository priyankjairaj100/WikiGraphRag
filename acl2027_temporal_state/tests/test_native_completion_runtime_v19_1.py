"""Focused transport amendment controls, inheriting the frozen mocked lifecycle."""
import errno
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_completion_runtime_v19_1 as current
import test_native_completion_runtime_v19 as prior


class AmendedLifecycleControls(prior.LifecycleControls):
    def setUp(self):
        self.alias = patch.object(prior, 'rt', current)
        self.alias.start()
        self.addCleanup(self.alias.stop)
        super().setUp()

    def test_reuseaddr_set_before_unchanged_bind(self):
        result = self.run_one()
        self.assertEqual(result['status'], 'technical_passed')
        port = self.mocks[3].return_value.__enter__.return_value
        self.assertEqual([x[0] for x in port.method_calls], ['setsockopt', 'bind'])
        port.setsockopt.assert_called_once_with(current.socket.SOL_SOCKET, current.socket.SO_REUSEADDR, 1)
        port.bind.assert_called_once_with(('127.0.0.1', self.c['port']))

    def test_busy_port_errno_and_phase_retained_before_launch(self):
        self.mocks[3].return_value.__enter__.return_value.bind.side_effect = OSError(errno.EADDRINUSE, 'authored busy port')
        result = self.run_one()
        self.assertEqual(result['failure_stage'], 'port_bind_preflight')
        self.assertEqual(result['error_errno'], errno.EADDRINUSE)
        self.assertEqual(self.launch.call_count, 0)
        self.assertTrue(result['cleanup_confirmed'])

    def test_asset_error_is_distinct_from_port_error(self):
        self.mocks[0].side_effect = OSError(errno.EIO, 'authored verification I/O failure')
        result = self.run_one()
        self.assertEqual(result['failure_stage'], 'asset_verification')
        self.assertEqual(result['error_errno'], errno.EIO)
        self.assertEqual(self.launch.call_count, 0)
        self.assertEqual(self.mocks[3].call_count, 0)


if __name__ == '__main__':
    unittest.main()

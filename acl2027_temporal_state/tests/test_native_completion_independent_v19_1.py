"""Independent owned-loopback regression; native/HTTP/cgroup remain mocked.

Exercise the actual amended complete_one preflight, using only a kernel-chosen
ephemeral port. This establishes a mechanism, not the old uncaptured errno.
"""
import errno
from pathlib import Path
import socket
import unittest

import test_native_completion_runtime_v19_1 as owner


class IndependentTransportControls(unittest.TestCase):
    def test_live_listener_refusal_and_time_wait_rebind_actual_runtime(self):
        fixture = owner.AmendedLifecycleControls(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Remove only the socket mock; process/HTTP/cgroup hooks remain mocked.
        fixture.patches[3].stop()
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.settimeout(2)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        address = listener.getsockname()
        fixture.c['port'] = address[1]

        with socket.socket() as plain:
            with self.assertRaises(OSError) as captured:
                plain.bind(address)
            self.assertEqual(captured.exception.errno, errno.EADDRINUSE)
        refused = fixture.run_one(0)
        self.assertEqual(refused['status'], 'failed')
        self.assertEqual(refused['failure_stage'], 'port_bind_preflight')
        self.assertEqual(refused['error_errno'], errno.EADDRINUSE)
        self.assertFalse(refused['process_started'])
        self.assertTrue(refused['cleanup_confirmed'])
        self.assertEqual(fixture.launch.call_count, 0)
        self.assertEqual(fixture.endpoints, [])

        client = socket.socket()
        self.addCleanup(client.close)
        client.settimeout(2)
        client.connect(address)
        accepted, _ = listener.accept()
        self.addCleanup(accepted.close)
        accepted.settimeout(2)
        accepted.shutdown(socket.SHUT_WR)
        self.assertEqual(client.recv(1), b'')
        client.close()
        self.assertEqual(accepted.recv(1), b'')
        accepted.close()
        listener.close()

        states = [line.split() for line in Path('/proc/net/tcp').read_text().splitlines()[1:]]
        owned = [fields for fields in states if fields[1] == f'0100007F:{address[1]:04X}']
        self.assertTrue(any(fields[3] == '06' for fields in owned), 'owned TIME_WAIT must be observed')
        with socket.socket() as plain:
            with self.assertRaises(OSError) as captured:
                plain.bind(address)
            self.assertEqual(captured.exception.errno, errno.EADDRINUSE)
        accepted_result = fixture.run_one(1)
        self.assertEqual(accepted_result['status'], 'technical_passed')
        self.assertTrue(accepted_result['cleanup_confirmed'])
        self.assertEqual(fixture.launch.call_count, 1)  # Mock only; no OS process.
        self.assertEqual(fixture.endpoints.count('completion'), 1)  # Mock only.


if __name__ == '__main__':
    unittest.main()

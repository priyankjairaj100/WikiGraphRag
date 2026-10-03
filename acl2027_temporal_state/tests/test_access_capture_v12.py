"""Offline checks for capture-budget, timeout and distributable-metadata risks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('access_capture_v12', ROOT / 'scripts/capture_access_pilot_v12.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
TOOLS = {'curl': {'executable': '/usr/bin/curl'}, 'pdftotext': {'executable': '/usr/bin/pdftotext'}}


def source(i=0):
    return {'source_id': f'source_{i}', 'history_id': 'history', 'url': f'https://example.test/{i}'}


class AccessCaptureTests(unittest.TestCase):
    def protocol(self):
        return {'frozen_at_utc': '2026-10-03T00:00:00Z', 'sources': [source(i) for i in range(8)],
                'max_attempts_per_url': 1, 'network_timeout_seconds': 30,
                'connect_timeout_seconds': 10, 'maximum_payload_bytes': 8388608,
                'maximum_redirects': 5, 'parallelism': 1}

    def test_protocol_guard_and_duplicate_urls(self):
        p = self.protocol()
        self.assertEqual(len(capture.validate_protocol(p)), 8)
        p['network_timeout_seconds'] = 31
        with self.assertRaises(ValueError):
            capture.validate_protocol(p)
        p = self.protocol()
        p['sources'][1]['url'] = p['sources'][0]['url']
        with self.assertRaises(ValueError):
            capture.validate_protocol(p)

    def test_url_and_source_path_escape_rejected(self):
        for key, value in [('source_id', '../escape'), ('url', 'http://example.test'),
                           ('url', 'https://user:password@example.test')]:
            p = self.protocol()
            p['sources'][0][key] = value
            with self.assertRaises(ValueError):
                capture.validate_protocol(p)

    def test_explicit_transfer_guards_and_disabled_curl_config(self):
        cmd = capture.curl_command(source(), Path('/tmp/example'), TOOLS)
        self.assertEqual(cmd[:2], ['/usr/bin/curl', '-q'])
        self.assertEqual(cmd[cmd.index('--max-time') + 1], '30')
        self.assertEqual(cmd[cmd.index('--max-filesize') + 1], '8388608')
        self.assertEqual(cmd[cmd.index('--proto-redir') + 1], '=https')
        self.assertNotIn('--retry', cmd)

    def test_timeout_preserves_partial_and_next_url_can_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def timeout(cmd, **kwargs):
                Path(cmd[cmd.index('--output') + 1]).write_bytes(b'partial')
                raise subprocess.TimeoutExpired(cmd, 35, output=b'private', stderr=b'private')
            with patch.object(capture.subprocess, 'run', side_effect=timeout):
                failed = capture.capture_one(source(), 0, root, TOOLS)
            self.assertEqual(failed['outer_failure_type'], 'TimeoutExpired')
            self.assertEqual((root / 'source_0/response.bin').read_bytes(), b'partial')
            self.assertFalse(failed['transport_success'])
            with patch.object(capture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7, b'{}', b'')):
                next_result = capture.capture_one(source(1), 1, root, TOOLS)
            self.assertEqual(next_result['curl_returncode'], 7)
            with self.assertRaises(FileExistsError):
                capture.capture_one(source(), 0, root, TOOLS)

    def test_error_page_parses_but_never_admitted_and_private_data_stays_external(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def fake(cmd, **kwargs):
                Path(cmd[cmd.index('--output') + 1]).write_bytes(b'<html><title>Access Denied</title><body>PRIVATE BODY</body></html>')
                Path(cmd[cmd.index('--dump-header') + 1]).write_text('Set-Cookie: PRIVATE COOKIE')
                transfer = {'http_code': 403, 'content_type': 'text/html', 'num_redirects': 0,
                            'headers': 'PRIVATE HEADER', 'url_effective': 'PRIVATE REDIRECT'}
                return subprocess.CompletedProcess(cmd, 0, json.dumps(transfer).encode(), b'PRIVATE STDERR')
            with patch.object(capture.subprocess, 'run', side_effect=fake):
                result = capture.capture_one(source(), 0, root, TOOLS)
            self.assertTrue(result['transport_success'])
            self.assertTrue(result['mechanical_parse_success'])
            self.assertFalse(result['http_success'])
            self.assertFalse(result['provisional_usability'])
            self.assertFalse(result['complete_source_verified'])
            self.assertFalse(result['benchmark_admitted'])
            self.assertFalse(result['expected_title_check_applicable'])
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_oversize_partial_cannot_be_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def fake(cmd, **kwargs):
                with Path(cmd[cmd.index('--output') + 1]).open('wb') as stream:
                    stream.truncate(capture.LIMITS['max_response_bytes'] + 1)
                return subprocess.CompletedProcess(cmd, 0, b'{"http_code":200,"content_type":"text/plain"}', b'')
            with patch.object(capture.subprocess, 'run', side_effect=fake), patch.object(capture, 'parse_document') as parse:
                result = capture.capture_one(source(), 0, root, TOOLS)
            parse.assert_not_called()
            self.assertFalse(result['transport_success'])
            self.assertFalse(result['response_within_byte_cap'])


if __name__ == '__main__':
    unittest.main()

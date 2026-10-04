"""Storage-only authored amendment tests; no actual source corpus loaded."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('table_storage', ROOT/'scripts/build_table_views_v16_1.py')
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)

class StorageTests(unittest.TestCase):
    def test_roundtrip_and_deterministic_container_independent_of_filename(self):
        records = [{'row': 'é 量\u00a0', 'ordinals': [1, 2]}, {'end': True}]
        expected = b''.join(store.base.encoded(r) for r in records)
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            a, b = Path(directory)/'a.gz', Path(directory)/'b.gz'
            first = store.gzip_records(a, records, store.CompressedBudget(1_000_000))
            second = store.gzip_records(b, records, store.CompressedBudget(1_000_000))
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(gzip.decompress(a.read_bytes()), expected)
            self.assertEqual(first, second)
            self.assertEqual(first['uncompressed_sha256'], store.base.sha(expected))
            self.assertEqual(first['uncompressed_bytes'], len(expected))
            self.assertEqual(store.decompressed_receipt(a), {k:first[k] for k in ('uncompressed_bytes','uncompressed_sha256')})

    def test_shared_compressed_budget_never_exceeds_cap(self):
        budget = store.CompressedBudget(7)
        raw = io.BytesIO(); sink = budget.sink(raw)
        sink.write(b'12345')
        with self.assertRaises(store.base.OutputLimitExceeded):
            sink.write(b'678')
        self.assertEqual(raw.getvalue(), b'12345')
        self.assertEqual(budget.bytes, 5)

    def test_gzip_cap_failure_remains_explicit(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            path = Path(directory)/'small.gz'; budget = store.CompressedBudget(5)
            with self.assertRaises(store.base.OutputLimitExceeded):
                store.gzip_records(path, [{'x':'value'}], budget)
            self.assertLessEqual(path.stat().st_size, 5)

    def test_private_archive_preserves_every_original_byte(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            root = Path(directory); original = root/'x.jsonl'; content=b'{"authored":"fixture"}\n'
            original.write_bytes(content)
            result = root/'original_result.json'; result.write_text(json.dumps({'records':[{'external_records':{'filename':original.name,'bytes':len(content),'sha256':store.base.sha(content),'complete':False}}]}))
            original_result = result.read_bytes()
            archive = store.archive_attempt(result, root, root/'archive.json')
            self.assertFalse(original.exists())
            self.assertEqual(gzip.decompress((root/'x.jsonl.gz').read_bytes()), content)
            self.assertEqual(result.read_bytes(), original_result)
            self.assertFalse(archive['files'][0]['original_complete'])
            self.assertTrue(archive['files'][0]['restore_verified'])

    def test_archive_hash_mismatch_is_detected_before_mutation(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            root=Path(directory); original=root/'x.jsonl';original.write_bytes(b'changed')
            result=root/'result.json';result.write_text(json.dumps({'records':[{'external_records':{'filename':original.name,'bytes':7,'sha256':'0'*64,'complete':False}}]}))
            with self.assertRaises(store.base.ViewError):
                store.archive_attempt(result,root,root/'archive.json')
            self.assertTrue(original.exists());self.assertFalse((root/'x.jsonl.gz').exists())

    def test_original_complete_and_partial_byte_stream_lineage(self):
        raw=b'first record\nsecond record\n'
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            path=Path(directory)/'full.gz';path.write_bytes(gzip.compress(raw,mtime=0))
            complete={'bytes':len(raw),'sha256':store.base.sha(raw),'complete':True}
            partial={'bytes':13,'sha256':store.base.sha(raw[:13]),'complete':False}
            self.assertEqual(store.verify_original_prefix(path,complete),'complete_stream_exact_match')
            self.assertEqual(store.verify_original_prefix(path,partial),'partial_prefix_exact_match')
            with self.assertRaises(store.base.ViewError):
                store.verify_original_prefix(path,dict(partial,complete=True))
            with self.assertRaises(store.base.ViewError):
                store.verify_original_prefix(path,dict(partial,sha256='0'*64))

    def test_compression_runtime_is_explicit(self):
        receipt=store.compression_runtime()
        self.assertIn('zlib_runtime_version',receipt)
        self.assertTrue(receipt['additional_files'])

    def test_existing_compressed_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            path=Path(directory)/'x.gz';path.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                store.gzip_records(path,[],store.CompressedBudget(100))
            self.assertEqual(path.read_bytes(),b'keep')

if __name__ == '__main__':
    unittest.main()

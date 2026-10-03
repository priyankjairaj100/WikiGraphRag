"""Mixed provenance must not promote unversioned candidates to pinned ones."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from test_scored_io_v06 import unversioned_fixture
from temporal_state.corrections import CorrectionPolicy
from temporal_state.correction_io import (dump_correction_cache,
    load_correction_cache, make_correction_cache)
from temporal_state.scored_io import dump_cache, load_cache, make_cache


def mixed_fixture():
    problem, claims, aliases, provenance = unversioned_fixture()
    description = ('Fixed next-token Yes minus No conditional log probabilities; '
                   'pinned scorer asset; unversioned frozen candidates; no calibration claim.')
    provenance.update(evidence_type='pinned_scorer_unversioned_candidates',
                      scorer_model_id='example/model/model.gguf',
                      scorer_model_revision='c' * 64, score_definition=description)
    return replace(problem, score_provenance=description), claims, aliases, provenance


class MixedProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'cache.json'
        self.parts = mixed_fixture()

    def resign(self, data):
        data.pop('digest', None)
        data['digest'] = sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.path.write_text(json.dumps(data))

    def test_roundtrip_binds_scorer_asset_without_inventing_candidate_revision(self):
        cache = make_cache(*self.parts)
        dump_cache(cache, self.path)
        self.assertEqual(json.loads(self.path.read_text())['schema_version'], '0.7')
        restored = load_cache(self.path)
        self.assertEqual(restored.digest, cache.digest)
        self.assertIsNone(restored.provenance['candidate_model_revision'])
        self.assertEqual(restored.provenance['scorer_model_revision'], 'c' * 64)

    def test_candidate_revision_must_remain_explicitly_unknown(self):
        problem, claims, aliases, provenance = self.parts
        for value in ('a' * 64, 'unknown', 'main', '', False, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_cache(problem, claims, aliases,
                    {**provenance, 'candidate_model_revision': value})

    def test_scorer_asset_revision_must_be_lowercase_full_sha256(self):
        problem, claims, aliases, provenance = self.parts
        for value in (None, 'main', 'unknown', 'a' * 40, 'A' * 64, 'g' * 64,
                      'a' * 63, 'a' * 65, '', False, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_cache(problem, claims, aliases,
                    {**provenance, 'scorer_model_revision': value})

    def test_model_identity_and_prompt_bindings_required(self):
        problem, claims, aliases, provenance = self.parts
        for field in ('candidate_model_id', 'scorer_model_id', 'prompt_sha256', 'config_sha256'):
            for value in (None, '', False):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    make_cache(problem, claims, aliases, {**provenance, field: value})

    def test_schema_kind_cannot_be_relabelled_even_after_resigning(self):
        for version in ('0.4', '0.6'):
            dump_cache(make_cache(*self.parts), self.path)
            data = json.loads(self.path.read_text())
            data['schema_version'] = version
            self.resign(data)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'evidence type does not match'):
                load_cache(self.path)

    def test_unversioned_and_fully_pinned_contracts_stay_distinct(self):
        problem, claims, aliases, provenance = self.parts
        for kind in ('unversioned_model_scores', 'pinned_model_scores'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                make_cache(problem, claims, aliases, {**provenance, 'evidence_type': kind})

    def test_asset_change_changes_digest_and_stale_changes_fail(self):
        cache = make_cache(*self.parts)
        problem, claims, aliases, provenance = self.parts
        self.assertNotEqual(cache.digest, make_cache(problem, claims, aliases,
            {**provenance, 'scorer_model_revision': 'd' * 64}).digest)
        dump_cache(cache, self.path)
        data = json.loads(self.path.read_text())
        data['provenance']['scorer_model_revision'] = 'd' * 64
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            load_cache(self.path)

    def test_correction_policy_provenance_is_not_upgraded_by_scorer(self):
        cache = make_correction_cache(make_cache(*self.parts), CorrectionPolicy())
        dump_correction_cache(cache, self.path)
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw['base_cache']['schema_version'], '0.7')
        restored = load_correction_cache(self.path)
        self.assertEqual(restored.digest, cache.digest)
        self.assertEqual(restored.provenance['evidence_type'], 'authored_diagnostic')
        self.assertIsNone(restored.provenance['policy_builder_revision'])

    def test_existing_v06_natural_cache_remains_byte_identical(self):
        path = Path(__file__).resolve().parents[1] / 'data/natural_model_pilot_v06/shared_cache.json'
        before = path.read_bytes()
        self.assertEqual(sha256(before).hexdigest(),
                         '6b741723fc676c3fb76f004b95c5112cab921eb264adaa6a1301b914d82ed9e9')
        dump_correction_cache(load_correction_cache(path), self.path)
        self.assertEqual(before, self.path.read_bytes())


if __name__ == '__main__':
    unittest.main()

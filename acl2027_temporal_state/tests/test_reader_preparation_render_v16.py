"""Authored controls for evidence-bound paper rendering; no natural QA inputs."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/render_reader_preparation_v16.py'
SPEC = importlib.util.spec_from_file_location('paper_renderer_v16', SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class RenderBindingControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/dev/shm')
        self.root = Path(self.temp.name)
        self.result = self.root / 'authored.json'
        self.result.write_text(json.dumps({'count': 3, 'planned': 4, 'status': 'complete'}))
        self.root_patch = patch.object(MOD, 'ROOT', self.root)
        self.root_patch.start()
        self.doc = {
            'schema_version': 'reader_preparation_manuscript_v16',
            'status': 'completed_preparation_checkpoint',
            'claims_scope': dict(novelty=False, natural_qa_gain=False,
                                 full_xbrl_conformance=False, generally_strong_reader=False,
                                 browser_fidelity_verified=False),
            'abstract': 'Authored renderer check.', 'sections': [],
            'tables': {'t': {'rows': [['count', '3'], ['coverage', '3 / 4']]}},
            'evidence_bindings': {'authored.json': MOD.sha(self.result)},
            'cell_bindings': [
                {'table': 't', 'row': 0, 'column': 1, 'source': 'authored.json', 'keys': ['count']},
                {'table': 't', 'row': 1, 'column': 1, 'format': '{0} / {1}',
                 'components': [{'source': 'authored.json', 'keys': ['count']},
                                {'source': 'authored.json', 'keys': ['planned']}]},
            ],
            'execution_bindings': [{'source': 'authored.json', 'expected_status': 'complete'}],
            'terminal_requirements': [{'name': 'authored', 'terminal': True}],
        }

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_valid_bound_receipt_and_formatted_cells(self):
        report = MOD.validate(self.doc)
        self.assertEqual(report['table_cells_checked'], 2)
        self.assertEqual(report['evidence_hashes_checked'], 1)

    def test_modified_evidence_refused(self):
        self.result.write_text('{"count":300}')
        with self.assertRaisesRegex(ValueError, 'digest changed'):
            MOD.validate(self.doc)

    def test_wrong_numeric_cell_refused(self):
        self.doc['tables']['t']['rows'][0][1] = '4'
        with self.assertRaisesRegex(ValueError, 'differs from bound result'):
            MOD.validate(self.doc)

    def test_wrong_formatted_denominator_refused(self):
        self.doc['tables']['t']['rows'][1][1] = '3 / 3'
        with self.assertRaisesRegex(ValueError, 'differs from bound result'):
            MOD.validate(self.doc)

    def test_wrong_execution_state_refused(self):
        self.doc['execution_bindings'][0]['expected_status'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'Execution status'):
            MOD.validate(self.doc)

    def test_unfinished_terminal_requirement_refused(self):
        self.doc['terminal_requirements'][0]['terminal'] = False
        with self.assertRaisesRegex(ValueError, 'terminal evidence'):
            MOD.validate(self.doc)
        self.assertEqual(MOD.validate(self.doc, draft=True)['status'], 'draft')

    def test_draft_status_refused_for_final(self):
        self.doc['status'] = 'draft_preparation_checkpoint'
        with self.assertRaisesRegex(ValueError, 'terminal preparation'):
            MOD.validate(self.doc)

    def test_pending_text_refused_for_final(self):
        self.doc['abstract'] = 'Result pending.'
        with self.assertRaisesRegex(ValueError, 'unfinished text'):
            MOD.validate(self.doc)

    def test_unbound_number_refused(self):
        self.doc['tables']['t']['rows'].append(['invented', '99'])
        with self.assertRaisesRegex(ValueError, 'Unbound numerical'):
            MOD.validate(self.doc)

    def test_duplicate_binding_refused(self):
        self.doc['cell_bindings'].append(copy.deepcopy(self.doc['cell_bindings'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate table-cell'):
            MOD.validate(self.doc)

    def test_scope_overclaim_refused(self):
        self.doc['claims_scope']['browser_fidelity_verified'] = True
        with self.assertRaisesRegex(ValueError, 'limited scope'):
            MOD.validate(self.doc)


if __name__ == '__main__':
    unittest.main()

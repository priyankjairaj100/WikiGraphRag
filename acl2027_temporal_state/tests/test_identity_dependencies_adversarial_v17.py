"""Independent authored-only identity-selector provenance controls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import select_identity_dependencies_v17 as selector


def source(body):
    return ('<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
            'xmlns:dei="http://xbrl.sec.gov/dei/2099"><body>' + body + '</body></html>').encode()


def field(value):
    return '<p><ix:nonNumeric name="dei:DocumentType">' + value + '</ix:nonNumeric></p>'


class IndependentIdentityControls(unittest.TestCase):
    def select(self, body):
        raw = source(body)
        return selector.select_source(raw, {'source_sha256': hashlib.sha256(raw).hexdigest(),
                                           'source_path': 'authored.html', 'source_bytes': len(raw)})

    def test_mixed_empty_and_nonempty_declarations_are_unavailable(self):
        public, _ = self.select(field('Annual Report') + field(' &#160; '))
        row = next(x for x in public['identity_fields'] if x['field'] == 'DocumentType')
        self.assertEqual(row['status'], 'unavailable')

    def test_equal_repeated_nonempty_declarations_stay_eligible(self):
        public, _ = self.select(field('Annual Report') + field('Annual&#160;Report'))
        row = next(x for x in public['identity_fields'] if x['field'] == 'DocumentType')
        self.assertEqual(row['status'], 'candidates_pending_render_and_review')
        self.assertEqual(len(row['selected_candidate_blocks']), 2)

    def run_mutated_protocol(self, mutation):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
            base = Path(directory)
            root = base / 'repository' / 'research'
            root.mkdir(parents=True)
            source_root = base / 'authored_sources'
            source_root.mkdir()
            raw = source(field('Annual Report'))
            checksum = hashlib.sha256(raw).hexdigest()
            parent_sources = []
            for index in range(12):
                filename = f'authored_{index}.html'
                (source_root / filename).write_bytes(raw)
                parent_sources.append({'source_path': filename, 'external_filename': filename,
                                       'expected_sha256': checksum, 'expected_bytes': len(raw)})
            for relative in set(selector.CODE) | set(selector.INPUTS):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('{}')
            (root / selector.INPUTS[0]).write_text(json.dumps({'sources': parent_sources}))
            code = {name: selector.digest(root / name) for name in selector.CODE}
            inputs = {name: selector.digest(root / name) for name in selector.INPUTS}
            review = root / 'results' / 'authored_review.json'
            review.parent.mkdir(exist_ok=True)
            review.write_text(json.dumps({'status': 'passed_authored_review',
                                          'open_blockers': [], 'code_bindings': code}))
            protocol = {'schema_version': 'identity_dependency_selection_v17',
                        'rule': selector.RULE, 'code_bindings': code, 'input_bindings': inputs,
                        'sources': [{'source_path': s['source_path'], 'external_filename': s['external_filename'],
                                     'source_sha256': s['expected_sha256'], 'source_bytes': s['expected_bytes']}
                                    for s in parent_sources],
                        'source_denominator': 12, 'required_field_denominator': 36,
                        'review_path': 'results/authored_review.json', 'review_sha256': selector.digest(review),
                        'runtime': {'python': sys.version, 'lxml': list(selector.etree.LXML_VERSION)}}
            mutation(protocol, review)
            protocol_path = base / 'authored_protocol.json'
            protocol_path.write_text(json.dumps(protocol))
            with patch.object(selector, 'ROOT', root):
                return selector.run(protocol_path, source_root, base / 'private_outputs', base / 'result.json')

    def test_empty_source_list_cannot_report_twelve_source_completion(self):
        with self.assertRaises(ValueError):
            self.run_mutated_protocol(lambda p, r: p.update(sources=[]))

    def test_omitted_code_and_input_bindings_fail_closed(self):
        with self.assertRaises(ValueError):
            self.run_mutated_protocol(lambda p, r: p.update(code_bindings={}, input_bindings={}, sources=[]))

    def test_unclosed_review_cannot_be_substituted_at_execution(self):
        def mutate(protocol, review):
            review.write_text(json.dumps({'status': 'pending', 'open_blockers': ['authored blocker']}))
            protocol['review_sha256'] = selector.digest(review)
        with self.assertRaises(ValueError):
            self.run_mutated_protocol(mutate)


if __name__ == '__main__':
    unittest.main()

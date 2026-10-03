"""Packet masking and baseline-reuse gates; no research references are read."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import prepare_dense_support_v14 as packet


class PacketContracts(unittest.TestCase):
    def fixture(self, directory):
        qs = [{'question_id': f'q{i}', 'history_id': f'h{i//4}', 'question': f'Source question {i}?'} for i in range(8)]
        questions = directory/'questions.json'; packet.write_new(questions, {'questions': qs})
        current, baseline = [], []
        for q in qs:
            for number, c in enumerate(('ordinary', 'paired', 'parent', 'closure')):
                text = f'  exact source {q["question_id"]}   block {number}\n'
                baseline.append({**q, 'condition': c, 'context': text, 'context_sha256': packet.text_sha(text)})
            for number, c in enumerate(packet.CONDITIONS):
                if c.startswith('bm25_'):
                    text = next(r['context'] for r in baseline if r['question_id'] == q['question_id'] and r['condition'] == c.removeprefix('bm25_'))
                else: text = f'  alternative source {q["question_id"]}   block {number}\n'
                current.append({**q, 'condition': c, 'context': text, 'context_sha256': packet.text_sha(text)})
        paths = {}
        for name, rows in (('current', current), ('baseline', baseline)):
            path = directory/f'{name}.jsonl'; path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            metadata = directory/f'{name}_metadata.json'
            packet.write_new(metadata, {'external_contexts_sha256': packet.sha(path), 'questions_sha256': packet.sha(questions),
                                        'records': [{k: r[k] for k in ('question_id', 'condition', 'context_sha256')} for r in rows]})
            paths[name] = path; paths[name+'_metadata'] = metadata
        support = directory/'support.json'
        packet.write_new(support, {'records': [{'question_id': r['question_id'], 'context_sha256': r['context_sha256']} for r in baseline]})
        refs = {}
        for domain, offset in (('plug', 0), ('opera', 4)):
            refs[domain] = directory/f'{domain}_refs.json'
            packet.write_new(refs[domain], {'references': [{'question_id': f'q{i}', 'facts': [{'fact_id': 'f', 'text': 'synthetic contract fixture'}]} for i in range(offset, offset+4)]})
        return paths, support, questions, refs, current

    def test_packets_exclude_answers_and_labels_preserve32_new_and16_reuse(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent.parent/'tmp') as temporary:
            d = Path(temporary); paths, support, questions, refs, rows = self.fixture(d)
            with patch.object(packet, 'REFERENCE_SHA256', {k: packet.sha(v) for k, v in refs.items()}):
                result = packet.prepare(paths['current'], paths['current_metadata'], paths['baseline'], paths['baseline_metadata'],
                                        support, questions, refs, d/'packets', d/'manifest.json')
            self.assertEqual(result['new_case_count'], 32)
            self.assertEqual(len(result['baseline_contexts_reused']), 16)
            self.assertEqual(result['all_retrieval_contexts_preserved'], 48)
            for domain in ('plug', 'opera'):
                output = packet.read(d/'packets'/f'context_support_{domain}.json')
                self.assertEqual(len(output['cases']), 16)
                for case in output['cases']:
                    self.assertEqual(set(case), {'case_id', 'question_id', 'question', 'reference', 'received_context', 'context_sha256'})
                    self.assertEqual(packet.text_sha(case['received_context']), case['context_sha256'])
                    self.assertTrue(case['received_context'].startswith('  '))

    def test_source_difference_blocks_reuse_before_packets_written(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent.parent/'tmp') as temporary:
            d = Path(temporary); paths, support, questions, refs, rows = self.fixture(d)
            rows[0]['context'] += 'different source'
            rows[0]['context_sha256'] = packet.text_sha(rows[0]['context'])
            paths['current'].write_text(''.join(json.dumps(r)+'\n' for r in rows))
            metadata = packet.read(paths['current_metadata']); metadata['external_contexts_sha256'] = packet.sha(paths['current'])
            metadata['records'][0]['context_sha256'] = rows[0]['context_sha256']
            paths['current_metadata'].write_text(json.dumps(metadata))
            with patch.object(packet, 'REFERENCE_SHA256', {k: packet.sha(v) for k, v in refs.items()}):
                with self.assertRaises(ValueError):
                    packet.prepare(paths['current'], paths['current_metadata'], paths['baseline'], paths['baseline_metadata'],
                                   support, questions, refs, d/'packets', d/'manifest.json')
            self.assertFalse((d/'packets').exists())
            self.assertFalse((d/'manifest.json').exists())


if __name__ == '__main__': unittest.main()

"""Deterministic corpus/retrieval contract checks, not task performance tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load(name):
    path=Path(__file__).resolve().parents[1]/'scripts'/f'{name}.py'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus=load('build_paired_corpus_v13')
retrieval=load('retrieve_paired_v13')


class CorpusContracts(unittest.TestCase):
    def test_page_boundaries_and_empty_pages_preserved(self):
        self.assertEqual(corpus.split_pages('first\f\fthird\f',3),['first','','third'])
        with self.assertRaises(ValueError):
            corpus.split_pages('first\fthird\f',3)

    def test_chunk_overlap_has_no_redundant_tail(self):
        text=' '.join(str(i) for i in range(291))
        chunks=list(corpus.page_chunks(text))
        self.assertEqual([(a,b) for a,b,_ in chunks],[(0,160),(130,290),(260,291)])
        exact=list(corpus.page_chunks(' '.join(str(i) for i in range(290))))
        self.assertEqual(len(exact),2)
        self.assertEqual(chunks[0][2].split()[-30:],chunks[1][2].split()[:30])

    def test_transcription_requires_review_and_unambiguous_caption(self):
        table={'whole_table':True,'visual_review_performed':True,'insert_after_caption_containing':'Table 1:','text':'all rows'}
        self.assertIn('NOT NATIVE PDF TEXT',corpus.table_supplement('Table 1: caption\nbody',table))
        with self.assertRaises(ValueError):
            corpus.table_supplement('Table 1:\nTable 1:',table)
        with self.assertRaises(ValueError):
            corpus.table_supplement('Table 1:',{**table,'whole_table':False})


class RetrievalContracts(unittest.TestCase):
    def chunks(self):
        return [{'chunk_id':f'{doc}:p{i:03}:c01','document_id':doc,'version_order':version,'pdf_page':i,
                 'chunk_index':1,'text':text,'word_count':len(text.split())}
                for doc,version,text in [('old',1,'rare rare'),('new',2,'other')]
                for i in range(1,7)]

    def test_same_pool_scores_distinct_selection_and_identical_order(self):
        ranked=retrieval.rank_bm25(self.chunks(),'rare')
        ordinary=retrieval.select(ranked,'ordinary')
        paired=retrieval.select(ranked,'paired')
        self.assertEqual([c['document_id'] for _,c in ordinary],['old']*6)
        self.assertEqual([c['document_id'] for _,c in paired],['old']*3+['new']*3)
        scores={c['chunk_id']:score for score,c in ranked}
        self.assertTrue(all(scores[c['chunk_id']]==score for score,c in paired))
        self.assertIn('[old p.1 | chunk=old:p001:c01 | version=1]',retrieval.render(paired))

    def test_ties_and_unicode_tokenization(self):
        self.assertEqual(retrieval.tokens('ÉPS_x 1.2 δt'),['éps','x','1','2','δt'])
        ranked=retrieval.rank_bm25(list(reversed(self.chunks())),'absent')
        self.assertEqual([c['chunk_id'] for _,c in ranked],sorted(c['chunk_id'] for c in self.chunks()))

    def test_references_are_not_query_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'questions.json'
            path.write_text(json.dumps({'questions':[{'question_id':'q1','history_id':'h','question':'a query',
                                                     'answer':'secret answer','reference_pages':[99]}]}))
            self.assertEqual(retrieval.questions_only(path),[{'question_id':'q1','history_id':'h','question':'a query'}])


if __name__=='__main__':
    unittest.main()

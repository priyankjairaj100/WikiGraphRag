import unittest
from temporal_state.evidence_retrieval_v24 import Block,BM25,build_index,decompose,digest,merge_intervals,retrieve,words

class RetrievalTests(unittest.TestCase):
    def block(self,id,text,start=0,kind='text',table=None):
        return Block(id,'a.html','f'*64,kind,text,text,start,start+len(text),[[start,start+len(text)]],table)
    def test_all_rendered_headers_charged(self):
        b=self.block('1','alpha beta')
        result,rendered=retrieve([b],'alpha',words(b.render())-1,'lexical')
        self.assertEqual(result['rendered_words'],0)
        self.assertEqual(rendered,'')
    def test_no_nested_visible_text_duplication_utf8(self):
        src=b'<html xmlns="http://www.w3.org/1999/xhtml"><body><div>outer<p>Caf\xc3\xa9 alpha</p>tail</div><table><tr><td>2024</td><td>80</td></tr></table><div style="display:none">SECRET</div></body></html>'
        blocks,_=build_index(src,'u.html')
        text=' '.join(b.text for b in blocks)
        self.assertEqual(text.count('Caf\xe9'),1);self.assertIn('2024 80',text);self.assertNotIn('SECRET',text)
        for b in blocks:
            for a,z in b.intervals:self.assertNotIn(b'SECRET',src[a:z])
        leaf=next(b for b in blocks if 'Caf\xe9' in b.text)
        self.assertIn(b'Caf\xc3\xa9',src[leaf.intervals[0][0]:leaf.intervals[0][1]])
    def test_table_fallback_has_header_and_row(self):
        header=self.block('h','Years ended 2024 2023',10,table='T')
        row=self.block('r','unrecognized compensation 75 80',50,table='T')
        huge=self.block('huge','noise '*2000,100,table='T')
        budget=words(header.render())+words(row.render())
        result,rendered=retrieve([header,row,huge],'unrecognized compensation',budget,'structural')
        # A huge table still permits the selected row with charged column headers.
        self.assertLessEqual(result['rendered_words'],budget)
        self.assertNotIn('huge',result['selected_block_ids'])
        self.assertEqual(set(result['selected_block_ids']),{'h','r'})
    def test_generic_decomposition_ignores_family(self):
        qs=decompose('What changed and which recognition period? State the units.')
        self.assertIn('which recognition period',qs)
        self.assertNotIn('segment',str(qs))
    def test_source_date_rule_uses_actual_fy(self):
        early=self.block('early','target');early.source='misnamed_2023.html';early.fiscal_year='2022'
        late=self.block('late','target',100);late.source='misnamed_2024.html';late.fiscal_year='2023'
        result,_=retrieve([early,late],'What target for fiscal 2023?',100,'structural')
        self.assertEqual(result['selected_block_ids'],['late'])
        result,_=retrieve([early,late],'Compare fiscal 2022 and fiscal 2023 target.',100,'structural')
        self.assertEqual(set(result['selected_block_ids']),{'early','late'})
    def test_interval_union(self):
        self.assertEqual(merge_intervals([[5,10],[0,7],[15,20],[5,10]]),[[0,10],[15,20]])
    def test_bm25_relevance(self):
        b=[self.block('a','apples apples oranges'),self.block('b','airplane turbine',100)]
        self.assertEqual(BM25(b).rank('apples')[0][0],0)
    def test_typed_only_policy(self):
        b=[self.block('a','target'),self.block('b','target',10,'typed')]
        result,_=retrieve(b,'target',100,'typed')
        self.assertEqual(result['selected_block_ids'],['b'])
    def test_source_block_deduplication(self):
        b=self.block('a','target')
        result,rendered=retrieve([b],'target and target',100,'decomposition')
        self.assertEqual(result['selected_block_ids'],['a']);self.assertEqual(words(rendered),result['rendered_words'])
if __name__=='__main__':unittest.main()

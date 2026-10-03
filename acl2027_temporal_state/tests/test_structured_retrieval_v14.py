"""Source-boundary and budget contracts, not empirical retrieval benchmarks."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import build_structured_corpus_v14 as builder
import retrieve_structured_v14 as retrieval


def page(text,number=1):
    return builder.parse_page(text,{'document_id':'d','history_id':'h','version_order':1},number,builder.sha(text.encode()))


def seed_for(p,text,chunk=1):
    start=p['text'].index(text);stop=start+len(text)
    parents=[b['span_id'] for b in p['blocks'] if b['char_start']<stop and b['char_stop']>start]
    return {'chunk_id':f'd:p{p["pdf_page"]:03}:c{chunk:02}','document_id':'d','history_id':'h','version_order':1,
            'pdf_page':p['pdf_page'],'chunk_index':chunk,'char_start':start,'char_stop':stop,'native_text':text,
            'text':' '.join(text.split()),'word_count':len(text.split()),'parent_span_ids':parents}


def counter(question,context):
    # Deliberately artificial contract fixture, never a production token count.
    count=3600 if 'oversized' in context else 100+len(context.split())
    return {'input_tokens':count,'rendered_prompt_sha256':builder.sha((question+context).encode()),
            'token_ids_sha256':builder.sha(str(count).encode()),'tokenizer_identity':{'fixture_only':True}}


class StructuralBoundaries(unittest.TestCase):
    def test_exact_unicode_char_and_line_spans(self):
        p=page('HEADING\n\nδt  −8.0\nnext\u00a0word\n')
        for b in p['blocks']:
            text=p['text'][b['char_start']:b['char_stop']]
            self.assertEqual(builder.sha(text.encode()),b['text_sha256'])
        self.assertEqual(p['blocks'][1]['line_start_1based'],3)

    def test_standalone_units_bind_only_immediate_table(self):
        p=page('(In thousands)\n\n       2019     2020\nRevenue    4    5\nCosts      2    3\n')
        self.assertTrue(any(a['status']=='accepted' and a['rule_id']=='immediate_parenthetical_unit_declaration' for a in p['attachments']))
        q=page('(In thousands)\n\nThis intervening narrative introduces another subject and is not a table header.\n\n       2019     2020\nRevenue    4    5\nCosts      2    3\n')
        self.assertFalse(any(a['status']=='accepted' and a['relation_type']=='unit_context' for a in q['attachments']))

    def test_repeated_header_starts_new_table_region(self):
        p=page('       2019     2020\nRevenue    4    5\nCosts      2    3\n\n       2021     2022\nRevenue    7    8\nCosts      5    6\n')
        self.assertEqual(len(p['table_regions']),2)
        first,second=p['table_regions']
        self.assertLess(first['char_stop'],second['char_start'])
        second_links=[a for a in p['attachments'] if a['content_span']['span_id']==second['span_id'] and a['status']=='accepted']
        self.assertTrue(all(a['target_span']['char_start']>=second['char_start'] for a in second_links))

    def test_numeric_parenthesis_is_not_a_row_label_footnote(self):
        p=page('       A     B\nRevenue    (1)    5\nCosts       2    3\n\n(1) A separately labelled note.\n')
        notes=[a for a in p['attachments'] if a['relation_type']=='footnote_context']
        self.assertEqual(len(notes),1)
        self.assertEqual(notes[0]['status'],'unresolved')
        q=page('       A     B\nRevenue (1)    4    5\nCosts          2    3\n\n(1) A labelled row note.\n')
        self.assertTrue(any(a['relation_type']=='footnote_context' and a['status']=='accepted' for a in q['attachments']))

    def test_cross_page_requires_explicit_identity_and_continuation(self):
        p=page('Table 7\n       A     B\nRevenue    4    5\nCosts      2    3\n')
        q=page('Table 7 (continued)\n       A     B\nRevenue    7    8\nCosts      5    6\n',2)
        self.assertEqual(builder.cross_page_links(p,q)[0]['status'],'accepted')
        r=page('Notes (Continued)\n\n       A     B\nRevenue    7    8\nCosts      5    6\n',2)
        self.assertEqual(builder.cross_page_links(p,r)[0]['status'],'unresolved')


class RetrievalContracts(unittest.TestCase):
    def test_baseline_render_preserves_v13_layout_header_and_words(self):
        p=page('Revenue      4\nCost    2\n');s=seed_for(p,'Revenue      4\nCost    2')
        b={'seed':s,'spans':[retrieval.seed_span(s)],'accepted_attachment_ids':[],'unresolved_attachment_ids':[]}
        text,_,words=retrieval.render([b],'ordinary',{('d',1):p})
        self.assertEqual(text,'[d p.1 | chunk=d:p001:c01 | version=1]\nRevenue      4\nCost    2')
        self.assertEqual(words,4)

    def test_native_full_prompt_budget_drops_whole_bundle_without_backfill(self):
        p=page('oversized source\n\nsmall source\n');a=seed_for(p,'oversized source');b=seed_for(p,'small source',2)
        bundles=[retrieval.bundle(s,'ordinary',{},[]) for s in (a,b)]
        out=retrieval.pack(bundles,'ordinary',{('d',1):p},'query',counter)
        self.assertEqual([x['seed']['chunk_id'] for x in out['selected']],[b['chunk_id']])
        self.assertEqual(out['dropped'][0]['chunk_id'],a['chunk_id'])
        self.assertNotIn('oversized',out['context'])

    def test_union_never_fills_unselected_gap(self):
        base={'document_id':'d','history_id':'h','version_order':1,'pdf_page':1}
        spans=[{**base,'span_id':'a','char_start':0,'char_stop':3},{**base,'span_id':'b','char_start':7,'char_stop':9},
               {**base,'span_id':'c','char_start':1,'char_stop':4}]
        self.assertEqual([(s['char_start'],s['char_stop']) for s in retrieval.merge_spans(spans)],[(0,4),(7,9)])

    def test_closure_follows_accepted_links_only(self):
        p=page('HEADER\n\nsource phrase\n\nother phrase\n');s=seed_for(p,'source phrase')
        content=p['blocks'][1];yes=p['blocks'][0];no=p['blocks'][2]
        links=[builder.attachment(content,yes,'section_context','accepted','fixture','test'),
               builder.attachment(content,no,'unit_context','unresolved','fixture','test')]
        b=retrieval.bundle(s,'closure',{},links)
        self.assertIn(yes['span_id'],[a['span_id'] for a in b['spans']])
        self.assertNotIn(no['span_id'],[a['span_id'] for a in b['spans']])
        self.assertEqual(len(b['unresolved_attachment_ids']),1)


if __name__=='__main__':unittest.main()

#!/usr/bin/env python3
"""Source-only structural spans over the unchanged v13 augmented page corpus.

This deliberately conservative baseline does not infer a numeric cell's meaning.
It preserves native spans and emits accepted or unresolved context attachments.
No questions, references, grades, predictions or network are read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORD = re.compile(r'\S+')
NUMBER = re.compile(r'^[\s$€£()+\-−–—.,%\d]+$')
CAPTION = re.compile(r'^\s*(Table\s+([A-Za-z]?\d+[A-Za-z]?(?:[.\-]\d+)*))\b',re.I)
UNIT = re.compile(r'\b(?:in\s+(?:thousands|millions|billions)|amounts?\s+(?:are\s+)?(?:expressed|stated|shown)\s+in|except\s+(?:for\s+)?(?:share|per.share))\b',re.I)
INTRO = re.compile(r'\bfollowing\s+tables?\b',re.I)
FOOTNOTE = re.compile(r'^\s*(\([a-zA-Z0-9]+\)|\[[0-9]+\]|\*{1,3})\s+\S')


def sha(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def digest(path:Path)->str:
    return sha(path.read_bytes())


def lines_of(text:str)->list[dict]:
    rows=[];offset=0
    for number,line in enumerate(text.splitlines(keepends=True),1):
        rows.append({'line_1based':number,'char_start':offset,'char_stop':offset+len(line),'text':line})
        offset+=len(line)
    if offset!=len(text):raise ValueError('line offsets do not cover source')
    return rows


def cells(line:str)->list[tuple[int,str]]:
    # Multi-space column gaps or source pipe separators, never inferred cells.
    matches=list(re.finditer(r'\S(?:.*?\S)?(?=\s{2,}|\s*\||$)',line.rstrip('\r\n')))
    return [(m.start(),m.group().strip(' |')) for m in matches if m.group().strip(' |')]


def numeric(value:str)->bool:
    return bool(re.search(r'\d',value) and NUMBER.fullmatch(value))


def data_row(line:str)->bool:
    c=cells(line)
    return len(c)>=2 and not numeric(c[0][1]) and sum(numeric(value) for _,value in c[1:])>=1


def short_header(line:str)->bool:
    words=line.split()
    if not words or len(words)>22:return False
    return not data_row(line) and (len(cells(line))>=2 or (line.strip().endswith(':') and len(words)<=10) or bool(UNIT.search(line)) or bool(CAPTION.search(line))
                                 or bool(re.search(r'\b(?:year|years|period|periods|reported|restated|adjustments|uncertainties|columns)\b',line,re.I)))


def section_heading(text:str)->bool:
    words=text.split()
    if not words or len(words)>18 or any(data_row(line) for line in text.splitlines()):return False
    if UNIT.search(text) or CAPTION.search(text) or FOOTNOTE.search(text):return False
    letters=[c for c in text if c.isalpha()]
    return bool(re.match(r'^\s*\d+(?:\.\d+)*[.)]?\s+[A-Z]',text) or
                (letters and len(letters)>=6 and all(c.isupper() for c in letters)))


def line_span(page:dict, first:int,last:int, role:str, suffix:str)->dict:
    rows=page['lines'];start=rows[first]['char_start'];stop=rows[last]['char_stop']
    text=page['text'][start:stop]
    return {'span_id':f"{page['document_id']}:p{page['pdf_page']:03}:{suffix}",
            'document_id':page['document_id'],'history_id':page['history_id'],'version_order':page['version_order'],
            'pdf_page':page['pdf_page'],'char_start':start,'char_stop':stop,
            'line_start_1based':first+1,'line_stop_1based':last+1,'role':role,
            'text_sha256':sha(text.encode()),'word_count':len(text.split()),'source_page_sha256':page['source_page_sha256']}


def primitive_blocks(page:dict)->list[dict]:
    groups=[];start=None
    for i,row in enumerate(page['lines']):
        if row['text'].strip() and start is None:start=i
        if start is not None and (not row['text'].strip() or i==len(page['lines'])-1):
            end=i if row['text'].strip() else i-1
            groups.append((start,end));start=None
    blocks=[]
    for index,(first,last) in enumerate(groups,1):
        text=''.join(r['text'] for r in page['lines'][first:last+1])
        row_indices=[i for i in range(first,last+1) if data_row(page['lines'][i]['text'])]
        if len(row_indices)>=2:role='table_block'
        elif row_indices:role='table_fragment'
        elif FOOTNOTE.match(text):role='footnote_block'
        elif INTRO.search(text):role='table_introduction'
        elif section_heading(text):role='section_heading'
        elif all(short_header(r['text']) for r in page['lines'][first:last+1]):role='header_candidate'
        else:role='paragraph'
        block=line_span(page,first,last,role,f'b{index:03}')
        block['row_lines_1based']=[i+1 for i in row_indices]
        block['caption_ids']=sorted(set(m.group(2).lower() for m in CAPTION.finditer(text)))
        # Store candidate role labels, never generated descriptions of the source.
        block['contains_unit_expression']=bool(UNIT.search(text))
        block['contains_explicit_continued']=bool(re.search(r'\bcontinued\b',text,re.I))
        blocks.append(block)
    return blocks


def numeric_column_counts(page:dict,block:dict)->set[int]:
    return {sum(numeric(value) for _,value in cells(page['lines'][i-1]['text'])) for i in block['row_lines_1based']}


def table_regions(page:dict)->list[dict]:
    """Merge only adjacent aligned row blocks without a new header/section.

    Repeated year/column headers terminate a region; they may introduce another
    table even on the same page. No cross-page merge occurs here.
    """
    blocks=page['blocks'];regions=[];i=0
    while i<len(blocks):
        initial=blocks[i]
        if initial['role'] not in ('table_block','table_fragment'):i+=1;continue
        members=[initial];j=i+1
        signature=numeric_column_counts(page,initial)
        while j<len(blocks):
            candidate=blocks[j]
            if candidate['role'] not in ('table_block','table_fragment'):break
            first_row=candidate['row_lines_1based'][0]
            pre=page['lines'][candidate['line_start_1based']-1:first_row-1]
            # Only a short colon-ended category is allowed between table rows.
            category_only=all(row['text'].strip().endswith(':') and len(row['text'].split())<=10 for row in pre)
            if pre and not category_only:break
            if not signature.intersection(numeric_column_counts(page,candidate)):break
            members.append(candidate);j+=1
        region=line_span(page,members[0]['line_start_1based']-1,members[-1]['line_stop_1based']-1,'table_region',f't{len(regions)+1:03}')
        region['primitive_block_ids']=[b['span_id'] for b in members]
        region['row_lines_1based']=[line for b in members for line in b['row_lines_1based']]
        region['caption_ids']=initial['caption_ids']
        region['contains_explicit_continued']=initial['contains_explicit_continued']
        regions.append(region);i=j
    return regions


def projection(span:dict)->dict:
    keys=('span_id','document_id','history_id','version_order','pdf_page','char_start','char_stop',
          'line_start_1based','line_stop_1based','role','text_sha256','word_count','source_page_sha256')
    return {key:span[key] for key in keys}


def attachment(anchor:dict,target:dict|None,relation:str,status:str,rule:str,reason:str)->dict:
    if status not in ('accepted','unresolved'):raise ValueError('invalid relation status')
    key=f"{anchor['span_id']}|{target['span_id'] if target else 'none'}|{relation}|{rule}"
    return {'attachment_id':'a_'+sha(key.encode())[:20],'content_span':projection(anchor),
            'anchor_span':projection(anchor),'target_span':projection(target) if target else None,
            'relation_type':relation,'status':status,'rule_id':rule,'reason':reason,
            'evidence_locators':[projection(anchor)]+([projection(target)] if target else [])}


def same_page_links(page:dict)->tuple[list[dict],list[dict]]:
    blocks=page['blocks'];targets=[];links=[]
    latest_section=None
    for index,block in enumerate(blocks):
        text=page['text'][block['char_start']:block['char_stop']]
        if block['role']=='section_heading':latest_section=block;continue
        if latest_section and block['role'] not in ('header_candidate','footnote_block'):
            links.append(attachment(block,latest_section,'section_context','accepted','preceding_same_page_explicit_heading',
                                    'Same-page source section heading; no assertion about numeric scope.'))
    for block in page['table_regions']:
        index=next(i for i,b in enumerate(blocks) if b['span_id']==block['primitive_block_ids'][0])
        last_index=next(i for i,b in enumerate(blocks) if b['span_id']==block['primitive_block_ids'][-1])
        text=page['text'][block['char_start']:block['char_stop']]
        first=block['line_start_1based']-1;last=block['line_stop_1based']-1
        first_row=block['row_lines_1based'][0]-1
        header_rows=[i for i in range(first,first_row) if page['lines'][i]['text'].strip()]
        if header_rows:
            header=line_span(page,header_rows[0],header_rows[-1],'table_header_context',block['span_id'].split(':')[-1]+'h')
            targets.append(header)
            # Long prose is not silently promoted to a table header.
            safe=all(short_header(page['lines'][i]['text']) for i in header_rows)
            links.append(attachment(block,header,'table_header_context','accepted' if safe else 'unresolved',
                                    'same_block_pre_row_header_band' if safe else 'pre_row_band_contains_narrative',
                                    'Exact source band before first aligned data row; no inferred cell-to-year mapping.'))
        else:
            links.append(attachment(block,None,'table_header_context','unresolved','no_same_block_header',
                                    'No uniquely bound pre-row header band; do not borrow a later or nearest table header.'))
        # A unit declaration inside the verified pre-row band is source-contained.
        unit_rows=[i for i in header_rows if UNIT.search(page['lines'][i]['text'])]
        if unit_rows:
            unit=line_span(page,min(unit_rows),max(unit_rows),'unit_declaration_context',block['span_id'].split(':')[-1]+'u')
            targets.append(unit)
            links.append(attachment(block,unit,'unit_context','accepted' if all(short_header(page['lines'][i]['text']) for i in header_rows) else 'unresolved',
                                    'same_block_pre_row_unit','Unit source appears in pre-row band; preserve its exceptions and qualifiers verbatim.'))
        previous=blocks[index-1] if index else None
        if previous:
            previous_text=page['text'][previous['char_start']:previous['char_stop']]
            if previous['role']=='table_introduction' and UNIT.search(previous_text):
                links.append(attachment(block,previous,'unit_introduction_context','accepted','immediate_explicit_following_table_introduction',
                                        'Explicit following-table source introduction directly precedes this table block.'))
            elif previous['role']=='header_candidate' and UNIT.search(previous_text) and all(
                    row.strip().startswith('(') and row.strip().endswith(')') for row in previous_text.splitlines() if row.strip()):
                links.append(attachment(block,previous,'unit_context','accepted','immediate_parenthetical_unit_declaration',
                                        'Standalone parenthetical unit declaration directly precedes the table region with no intervening prose.'))
            elif previous['role']=='header_candidate':
                # A standalone header has no automatic scope association.
                links.append(attachment(block,previous,'table_header_context','unresolved','separate_header_block_unbound',
                                        'A nearby standalone header is a candidate, not a certified attachment.'))
        # An explicitly labelled note is attached only by a unique literal marker.
        following=blocks[last_index+1] if last_index+1<len(blocks) else None
        if following and following['role']=='footnote_block':
            note_text=page['text'][following['char_start']:following['char_stop']]
            marker=FOOTNOTE.match(note_text).group(1)
            references=sum(cells(page['lines'][line-1]['text'])[0][1].count(marker)
                           for line in block['row_lines_1based']
                           if re.search(r'[A-Za-z]',cells(page['lines'][line-1]['text'])[0][1]))
            links.append(attachment(block,following,'footnote_context','accepted' if references==1 else 'unresolved',
                                    'unique_literal_adjacent_footnote' if references==1 else 'unmatched_or_repeated_footnote_marker',
                                    'Adjacent labelled source note requires exactly one literal marker in this table block.'))
    return targets,links


def header_fingerprint(page:dict,block:dict)->list[str]:
    first=block['line_start_1based']-1;row=block['row_lines_1based'][0]-1
    return [' '.join(page['lines'][i]['text'].lower().split()) for i in range(first,row)
            if page['lines'][i]['text'].strip() and not CAPTION.match(page['lines'][i]['text'])]


def cross_page_links(previous:dict,current:dict)->list[dict]:
    before=previous['table_regions']
    after=current['table_regions']
    if not before or not after:return []
    left,right=before[-1],after[0]
    # Cross-page continuation is intentionally fail-closed. A generic repeated
    # page/notes heading is not a table identity or permission to inherit units.
    common=set(left['caption_ids']) & set(right['caption_ids'])
    explicit=bool(common and right['contains_explicit_continued'])
    compatible=bool(header_fingerprint(previous,left) and header_fingerprint(previous,left)==header_fingerprint(current,right))
    boundary_blocks=[b for b in previous['blocks'] if b['char_start']>left['char_stop']]+[b for b in current['blocks'] if b['char_stop']<right['char_start']]
    hard_boundary=any(b['role'] in ('section_heading','paragraph','table_introduction') for b in boundary_blocks)
    accepted=explicit and compatible and not hard_boundary
    links=[attachment(right,left,'table_continuation_context','accepted' if accepted else 'unresolved',
                      'matching_explicit_table_continued_and_header' if accepted else 'cross_page_continuation_not_certified',
                      'Requires matching explicit table ID, continued marker, identical header band and no intervening narrative/section. Notes-page continued labels alone do not qualify.')]
    return links


def parse_page(text:str,document:dict,page_number:int,expected_sha:str)->dict:
    if sha(text.encode())!=expected_sha:raise ValueError('page hash mismatch')
    page={'document_id':document['document_id'],'history_id':document['history_id'],
          'version_order':document['version_order'],'pdf_page':page_number,
          'source_page_sha256':expected_sha,'text':text,'lines':lines_of(text)}
    page['blocks']=primitive_blocks(page)
    page['table_regions']=table_regions(page)
    page['target_spans'],page['attachments']=same_page_links(page)
    return page


def build(corpus_manifest:Path,external_output:Path,metadata_output:Path)->dict:
    if external_output.resolve().is_relative_to(ROOT.parent):raise ValueError('source-bearing corpus must stay external')
    if external_output.exists() or metadata_output.exists():raise FileExistsError('new output paths required')
    original=json.loads(corpus_manifest.read_text());directory=Path(original['external_corpus_root'])
    chunks_path=directory/original['chunks_file']
    if digest(chunks_path)!=original['chunks_sha256']:raise ValueError('original chunk hash mismatch')
    chunks=[json.loads(line) for line in chunks_path.read_text().splitlines()]
    pages=[];lookup={}
    for document in original['documents']:
        previous=None
        for source_page in document['pages']:
            number=source_page['pdf_page'];path=directory/document['document_id']/f'p{number:03}.txt'
            page=parse_page(path.read_text(),document,number,source_page['corpus_page_sha256'])
            if previous:page['attachments'].extend(cross_page_links(previous,page))
            pages.append(page);lookup[(page['document_id'],number)]=page;previous=page
    for chunk in chunks:
        page=lookup[(chunk['document_id'],chunk['pdf_page'])];words=list(WORD.finditer(page['text']))
        start=words[chunk['word_start_0based']].start();stop=words[chunk['word_stop_exclusive']-1].end()
        native=page['text'][start:stop]
        if native.split()!=chunk['text'].split() or sha(chunk['text'].encode())!=chunk['text_sha256']:
            raise ValueError('seed differs from original frozen word sequence')
        chunk['char_start']=start;chunk['char_stop']=stop;chunk['native_text']=native
        chunk['parent_block_ids']=[b['span_id'] for b in page['blocks'] if b['char_start']<stop and b['char_stop']>start]
        region_for={member:region['span_id'] for region in page['table_regions'] for member in region['primitive_block_ids']}
        chunk['parent_span_ids']=sorted(set(region_for.get(block_id,block_id) for block_id in chunk['parent_block_ids']))
        if not chunk['parent_block_ids']:raise ValueError('nonempty chunk lacks parent source block')
    payload={'schema_version':'structured_corpus_v0.14','source_corpus_manifest_sha256':digest(corpus_manifest),
             'source_chunks_sha256':digest(chunks_path),'pages':pages,'seeds':chunks}
    external_output.parent.mkdir(parents=True,exist_ok=True)
    with external_output.open('x') as stream:json.dump(payload,stream,indent=2,ensure_ascii=False);stream.write('\n')
    public_pages=[]
    for page in pages:
        public_pages.append({k:v for k,v in page.items() if k not in ('text','lines') } |
                            {'line_count':len(page['lines']),'source_word_count':len(page['text'].split())})
    attachments=[link for page in pages for link in page['attachments']]
    report={'schema_version':'structured_corpus_manifest_v0.14','builder_sha256':digest(Path(__file__)),
            'source_corpus_manifest_path':str(corpus_manifest),'source_corpus_manifest_sha256':digest(corpus_manifest),
            'source_chunks_sha256':digest(chunks_path),'external_structured_corpus_path':str(external_output),
            'external_structured_corpus_sha256':digest(external_output),'pdf_pages':len(pages),'seed_chunks':len(chunks),
            'primitive_blocks':sum(len(p['blocks']) for p in pages),'attachment_counts':{s:sum(a['status']==s for a in attachments) for s in ('accepted','unresolved')},
            'table_regions':sum(len(p['table_regions']) for p in pages),
            'source_only':True,'questions_references_or_predictions_read':False,'pages':public_pages,
            'limitations':['Rule-based structural candidates require independent source-only audit; accepted is parser status, not human certification.',
                           'No numeric cell-to-column semantics inferred; supplied headers are exact source context.',
                           'Cross-page unit inheritance remains unresolved without explicit table-continuation evidence.',
                           'Original native PDF extraction and two model-assisted table transcriptions retain their known limitations.']}
    metadata_output.parent.mkdir(parents=True,exist_ok=True)
    with metadata_output.open('x') as stream:json.dump(report,stream,indent=2,ensure_ascii=False);stream.write('\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus-manifest',type=Path,required=True)
    parser.add_argument('--external-output',type=Path,required=True)
    parser.add_argument('--metadata-output',type=Path,required=True)
    args=parser.parse_args();result=build(args.corpus_manifest,args.external_output,args.metadata_output)
    print(json.dumps({k:result[k] for k in ('pdf_pages','seed_chunks','primitive_blocks','attachment_counts','external_structured_corpus_sha256')}))


if __name__=='__main__':main()

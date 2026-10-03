"""Deterministically derive bounded evidence from captured, current public bytes.

No network. Full response bodies, extracted HTML and full text are analysis-only.
Capsule offsets are Unicode code points in the named UTF-8 derived text artifact.
"""
from pathlib import Path
from html.parser import HTMLParser
import re, json, hashlib, sys
ROOT = Path(__file__).resolve().parent

def sha(b): return hashlib.sha256(b).hexdigest()

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out=[]; self.skip=[]
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','head','ix:hidden'}: self.skip.append(tag)
        if not self.skip and tag in {'div','p','li','br','tr','h1','h2','h3','h4'}: self.out.append('\n')
    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]: self.skip.pop()
            return
        if tag in {'div','p','li','tr','h1','h2','h3','h4'}: self.out.append('\n')
    def handle_data(self, value):
        if not self.skip: self.out.append(value)
    def text(self):
        return '\n'.join(line for line in (' '.join(x.split()) for x in ''.join(self.out).splitlines()) if line)+'\n'

def extract_html(b):
    p=TextExtractor();p.feed(b.decode('utf-8',errors='strict'));return p.text()

def item(source_id, parent, needles, paraphrase, embedded=False):
    raw_path=ROOT/(parent+'.response.bin');raw=raw_path.read_bytes()
    payload=raw; extraction={
      'script':'source_capture/derive_evidence.py','script_sha256':sha(Path(__file__).read_bytes()),
      'character_decoding':'UTF-8 strict',
      'parser':'Python standard-library HTMLParser(convert_charrefs=True)',
      'normalization':'Omit head/script/style/ix:hidden; block tags create newlines; collapse each line whitespace; remove empty lines; final LF.',
      'offset_unit':'Python Unicode code points in derived text, not raw HTML bytes',
      'python_version':sys.version.split()[0]}
    if embedded:
        matches=list(re.finditer(rb'<DOCUMENT>\s*<TYPE>EX-99\.1\s.*?<TEXT>(.*?)</TEXT>\s*</DOCUMENT>',raw,re.S))
        if len(matches)!=1: raise ValueError('Expected exactly one EX-99.1 text block')
        m=matches[0];payload=m[1]
        extraction.update(embedded_document_type='EX-99.1',raw_byte_start=m.start(1),raw_byte_end=m.end(1),embedded_html_sha256=sha(payload))
        (ROOT/(source_id+'.embedded.html')).write_bytes(payload)
    text=extract_html(payload)
    text_path=ROOT/(source_id+'.derived.txt');text_path.write_text(text,encoding='utf-8')
    quotes=[]
    for purpose,needle in needles:
        start=text.find(needle)
        if start<0: raise ValueError((source_id,needle))
        quotes.append({'purpose':purpose,'text':needle,'start':start,'end':start+len(needle),'occurrences_in_full_derived_text':text.count(needle)})
    count=sum(len(q['text'].split()) for q in quotes)
    if count>25: raise ValueError((source_id,count))
    return {'source_id':source_id,'representation':'selected_exact_excerpts_from_current_http_capture',
      'raw_parent_path':'source_capture/'+raw_path.name,'raw_parent_sha256':sha(raw),'raw_parent_bytes':len(raw),
      'derived_text_path':'source_capture/'+text_path.name,'derived_text_sha256':sha(text.encode()),
      'derived_text_characters':len(text),'extraction':extraction,'excerpts':quotes,'total_quoted_words':count,
      'context_omitted':True,'excerpt_selection':'Model-assisted development selection of this already-discovered correction; not a sampled or blinded test.',
      'factual_paraphrase':paraphrase,'historical_first_availability_verified':False,
      'full_raw_and_derived_text_distribution':'external_working_data_excluded_from_checkpoint; replay capture_public.py then derive_evidence.py'}

specs=[
 ('lyft_original_sec_exhibit','lyft_original_submission',[
  ('issuer_attribution','Lyft, Inc.'),('forecast_target_period','FY’24 Directional Commentary:'),
  ('forecast_metric','Adjusted EBITDA margin expansion (calculated as a percentage of Gross Bookings)'),
  ('reported_forecast_value','approximately 500 basis points year-over-year.')],
  'The original SEC exhibit gives a 500-basis-point year-over-year expansion forecast for adjusted EBITDA margin as a fraction of Gross Bookings for FY2024. It is a forecast, not a realized result.',True),
 ('lyft_corrected_sec_exhibit','lyft_amendment_submission',[
  ('issuer_attribution','Lyft, Inc.'),('forecast_target_period','FY’24 Directional Commentary:'),
  ('forecast_metric','Adjusted EBITDA margin expansion (calculated as a percentage of Gross Bookings)'),
  ('reported_forecast_value','approximately 50 basis points year-over-year.')],
  'The corrected exhibit in accession 0001759509-24-000015 gives 50 basis points for the same FY2024 adjusted EBITDA margin expansion forecast.',True),
 ('lyft_amendment_sec_report','lyft_amendment_sec_report',[
  ('issuer_attribution','Lyft, Inc.'),
  ('correction_authorization','to correct a clerical error in the 2024 directional commentary'),
  ('prior_record_type','to the Current Report on Form 8-K'),
  ('target_release_date','February 13, 2024')],
  'The SEC 8-K/A explanatory note explicitly identifies the earlier 8-K and February 13 press release, classifies the issue as a clerical error, and supplies a corrected Exhibit 99.1.',False),
 ('lyft_explicit_correction','lyft_explicit_correction',[
  ('issuer_attribution','Lyft, Inc.'),
  ('claim_location','Fifth bulleted list, third bullet of release should read:'),
  ('replacement_value','approximately 50 basis points year-over-year.'),
  ('replaced_value','approximately 500 basis points year-over-year.')],
  'The current issuer release explicitly replaces 500 with 50 in a precisely located forecast bullet. Its displayed 4:05pm EST timestamp does not authenticate when the corrected version first appeared.',False)
]

if __name__=='__main__':
    capsules=[item(*s) for s in specs]
    out={'schema_version':'0.5-evidence-capsules','status':'development_evidence_not_benchmark',
      'historical_availability_policy':'Explicit record/correction ordering can be established; no intraday public-access cutoff or original historical bytes claimed.',
      'gold_answers_created':0,'algorithm_scores_created':0,'sources':capsules}
    (ROOT/'evidence_capsules.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({c['source_id']:c['total_quoted_words'] for c in capsules}))

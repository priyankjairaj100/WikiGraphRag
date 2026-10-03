"""Reproducible public HTTP byte capture. No auth, cookies, retries or proxies configured."""
from datetime import datetime, timezone
import hashlib, json, pathlib, urllib.request, urllib.error
ROOT=pathlib.Path(__file__).resolve().parent
TARGETS={
 'lyft_corrected_sec_exhibit':'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000015/lyft-2023x12x31pressreleas.htm',
 'lyft_original_submission':'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000011/0001759509-24-000011.txt',
 'lyft_amendment_submission':'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000015/0001759509-24-000015.txt',
 'lyft_amendment_sec_index':'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000015/0001759509-24-000015-index.htm',
 'lyft_amendment_sec_report':'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000015/lyft-20240213.htm',
 'lyft_original_sec_exhibit':'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000011/lyft-2023x12x31pressreleas.htm',
 'lyft_original_sec_index':'https://www.sec.gov/Archives/edgar/data/1759509/000175950924000011/0001759509-24-000011-index.htm',
 'lyft_explicit_correction':'https://investor.lyft.com/news-events-presentations/press-releases/detail/119/correcting-and-replacing-lyft-announces-fourth-quarter-and-full-year-2023-results',
}
HEADER_ALLOW={'date','content-type','content-length','content-encoding','last-modified','etag','cache-control','expires','age','server','location','memento-datetime','link','x-archive-orig-date','x-archive-orig-last-modified','x-archive-orig-content-type'}

def capture(source_id,url):
 now=datetime.now(timezone.utc).isoformat()
 record={'source_id':source_id,'requested_url':url,'retrieval_started_utc':now,'request_method':'GET','request_headers':{'User-Agent':'TemporalResearch/0.5 public document verification','Accept-Encoding':'identity'},'representation':'http_response_entity_bytes_as_returned_by_urllib_no_text_normalization','historical_original_bytes_claimed':False,'response_headers':None,'status':None,'response_body_path':None,'response_body_sha256':None,'response_body_bytes':None,'network_error':None}
 request=urllib.request.Request(url,headers=record['request_headers'])
 try:
  with urllib.request.urlopen(request,timeout=30) as response:
   body=response.read(5_000_001)
   if len(body)>5_000_000: raise ValueError('Response exceeded 5 MB capture bound')
   record.update(status=response.status,final_url=response.url,response_headers={k:v for k,v in response.headers.items() if k.lower() in HEADER_ALLOW})
   path=ROOT/(source_id+'.response.bin'); path.write_bytes(body)
   record.update(response_body_path=str(path.relative_to(ROOT.parent)),response_body_sha256=hashlib.sha256(body).hexdigest(),response_body_bytes=len(body))
 except urllib.error.HTTPError as e:
  record.update(status=e.code,final_url=e.url,response_headers={k:v for k,v in e.headers.items() if k.lower() in HEADER_ALLOW},network_error='HTTPError: '+str(e))
 except Exception as e: record['network_error']=type(e).__name__+': '+str(e)
 record['retrieval_finished_utc']=datetime.now(timezone.utc).isoformat()
 (ROOT/(source_id+'.capture.json')).write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps({k:record[k] for k in ['source_id','status','response_body_bytes','response_body_sha256','network_error']}))
 return record
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('source_ids',nargs='*',choices=list(TARGETS))
 parser.add_argument('--output-dir',type=pathlib.Path,default=ROOT)
 args=parser.parse_args()
 ROOT=args.output_dir.resolve();ROOT.mkdir(parents=True,exist_ok=True)
 selected=args.source_ids or list(TARGETS)
 for source_id in selected: capture(source_id,TARGETS[source_id])

#!/usr/bin/env python3
"""Repair v1 HTML form exclusion from already captured bytes, no HTTP requests.

Some investor pages place the entire article inside an ASP.NET form. v2 retains
form text and adds the explicitly observed 'Site Unavailable' blocker title.
All v1 text, receipts, executed code and original manifest are preserved.
"""
import argparse
import importlib.util
import json
from pathlib import Path
from hashlib import sha256

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture_v08', ROOT/'scripts/capture_source_stream_v08.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def hashfile(p):
    return sha256(p.read_bytes()).hexdigest()


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--manifest',type=Path,default=ROOT/'data/source_stream_v08/capture_manifest.json')
    args=a.parse_args()
    m=json.loads(args.manifest.read_text())
    if 'extraction_repair' in m:
        raise SystemExit('Already repaired; refusing to overwrite the preserved derivation history.')
    before=args.manifest.with_name('capture_before_extraction_repair_v1.json')
    if before.read_bytes()!=args.manifest.read_bytes():
        raise SystemExit('Preserved v1 manifest mismatch')
    log=[]
    for s in m['sources']:
        folder=Path(s['local_fulltext_directory'])
        receipt=folder/'capture_receipt.json'
        receipt.rename(folder/'capture_receipt_v1.json')
        raw=Path(s['local_raw_path'])
        if hashfile(raw)!=s['raw_response_sha256']:
            raise SystemExit('Raw capture changed: '+s['source_id'])
        old=s['extracted_text_sha256']
        s['http_attempt_number']=2
        s['attempt_number']=2
        if s['extraction']['recipe']=='stdlib_HTMLParser_body_text_NFC_nonempty_lines':
            textpath=folder/'document.txt'
            if textpath.exists():
                textpath.rename(folder/'document.extraction_v1.txt')
            decoded,charset=mod.decode_html(raw.read_bytes(),s['content_type'])
            parser=mod.DocumentText()
            parser.feed(decoded)
            parser.close()
            text=parser.text()
            title=mod.normalized(''.join(parser.titles))
            textpath.write_text(text,encoding='utf-8',newline='\n')
            s['local_text_path']=str(textpath)
            s['extracted_text_sha256']=hashfile(textpath)
            s['extracted_text_bytes']=textpath.stat().st_size
            s['captured_title']=title
            s['extraction']={'recipe':'stdlib_HTMLParser_body_text_NFC_nonempty_lines','version':2,
                             'charset':charset,'excluded_tags':sorted(mod.DocumentText.SKIP)}
            s['content_usability']=mod.usability(s['http_status'],s['content_type'],title,text,s['expected_title'])
            if s['curl_returncode']:
                s['content_usability']['rejection_reasons'].append('curl_transfer_failed')
                s['content_usability']['provisional_content_usable']=False
            s['extraction_error']=None
        log.append({'source_id':s['source_id'], 'raw_response_sha256':s['raw_response_sha256'],
                    'previous_text_sha256':old,'final_text_sha256':s['extracted_text_sha256'],
                    'text_changed':old!=s['extracted_text_sha256'],
                    'html_recipe_version':s['extraction']['version'],
                    'raw_bytes_unchanged':True})
        receipt.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
    m['extraction_repair']={'performed_at':mod.now(), 'reason':'ASP.NET investor pages wrap all visible content in a form; retain visible form text deterministically for every HTML response. Add explicit Site Unavailable blocker title.',
                            'http_recapture_performed':False,
                            'prior_manifest_path':'data/source_stream_v08/capture_before_extraction_repair_v1.json',
                            'prior_manifest_sha256':hashfile(before),
                            'executed_retrieval_script_path':'data/source_stream_v08/executed_capture_v1.py.txt',
                            'executed_retrieval_script_sha256':hashfile(ROOT/'data/source_stream_v08/executed_capture_v1.py.txt'),
                            'repair_script_sha256':hashfile(Path(__file__)),
                            'updated_extractor_script_sha256':hashfile(ROOT/'scripts/capture_source_stream_v08.py'),
                            'derivations':log}
    m['capture_attempt_number']=2
    m['prior_attempts']=[{'manifest_path':'data/source_stream_v08/capture_attempt01_sandbox_failure.json',
                         'manifest_sha256':hashfile(ROOT/'data/source_stream_v08/capture_attempt01_sandbox_failure.json'),
                         'attempted_sources':39,'bytes_obtained':0,
                         'failure':'Sandbox could not connect to the configured browser-proxy; every curl exited7.'}]
    m['all_http_attempts_including_failed_sandbox']=78
    m['provisionally_usable_sources']=sum(s['content_usability']['provisional_content_usable'] for s in m['sources'])
    m['complete_provisional_histories']=sum(all(s['content_usability']['provisional_content_usable'] for s in m['sources'] if s['history_id']==h) for h in {s['history_id'] for s in m['sources']})
    m['delivery_order_status']='Capture manifest is not a delivery manifest; root separately freezes author-defined batches.'
    args.manifest.write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'attempted_sources':m['attempted_sources'],'provisional_usable_sources':m['provisionally_usable_sources'],
                      'complete_provisional_histories':m['complete_provisional_histories'],
                      'html_extractions_changed':sum(x['text_changed'] for x in log),
                      'manifest_sha256':hashfile(args.manifest)}))

if __name__=='__main__':
    main()

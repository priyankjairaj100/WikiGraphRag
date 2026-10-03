#!/usr/bin/env python3
"""Capture the preselected v0.9 current versions; never overwrite prior attempts.

Reuses the repaired v0.8 visible-body extractor, including form body content.
The frozen selection controls all requests. Full external texts stay outside ROOT.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = ROOT / 'scripts/capture_source_stream_v08.py'
spec = importlib.util.spec_from_file_location('capture_v08_repaired', EXTRACTOR)
capture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture_module)
capture_module.USER_AGENT = 'TemporalStateResearch/0.9 (public document research; no authentication)'


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, default=ROOT/'data/source_stream_v09/capture_selection_plan.json')
    p.add_argument('--manifest', type=Path, default=ROOT/'data/source_stream_v09/capture_manifest.json')
    p.add_argument('--fulltext-root', type=Path, required=True)
    args = p.parse_args()
    if args.manifest.exists():
        raise SystemExit('Refusing overwrite: each capture attempt requires a new manifest and external directory.')
    fulltext_root = args.fulltext_root.resolve()
    if fulltext_root == ROOT or ROOT in fulltext_root.parents:
        raise SystemExit('Full external sources must remain outside the distributable project.')
    plan = json.loads(args.plan.read_text())
    if plan['status'] != 'frozen_before_new_capture':
        raise SystemExit('Selection must be frozen before requests.')
    for label in ('frame', 'prior_v08_capture_manifest'):
        if capture_module.digest(ROOT/plan[label+'_path']) != plan[label+'_sha256']:
            raise SystemExit('Predeclared parent changed: '+label)
    ids = plan['history_order']
    if [h['history_id'] for h in plan['histories']] != ids or len(set(ids)) != len(ids):
        raise SystemExit('Invalid ordered history selection.')
    jobs = [(h, s, n) for h in plan['histories'] for n, s in enumerate(h['sources'])]
    if len(jobs) != plan['expected_sources'] or len({s['source_id'] for _,s,_ in jobs}) != len(jobs):
        raise SystemExit('Source count or unique IDs invalid.')
    if any((fulltext_root/s['source_id']).exists() for _,s,_ in jobs):
        raise SystemExit('One or more output directories already exist; preserve earlier attempts.')
    old = json.loads((ROOT/plan['prior_v08_capture_manifest_path']).read_text())
    prior = {s['source_id']:s for s in old['sources']}
    curl = subprocess.run(['curl', '--version'], capture_output=True, text=True, check=True)
    pdf = subprocess.run(['pdftotext', '-v'], capture_output=True, text=True, check=True)
    manifest = {
        'schema_version':'source_capture_v0.9', 'status':'capture_running',
        'started_at':capture_module.now(), 'ended_at':None,
        'selection_plan_path':str(args.plan), 'selection_plan_sha256':capture_module.digest(args.plan),
        'wrapper_script_sha256':capture_module.digest(Path(__file__)),
        'reused_extractor_path':'scripts/capture_source_stream_v08.py',
        'reused_extractor_sha256':capture_module.digest(EXTRACTOR),
        'expected_source_count':len(jobs), 'expected_history_count':len(ids),
        'raw_v08_restored':False,
        'scope':'Fresh current public responses, not restored v0.8 bytes or historical originals; no benchmark admission.',
        'http_request_policy':{'max_parallel':4,'timeout_seconds':30,'max_response_bytes':8*1024*1024,
            'max_redirects':5,'connect_timeout_seconds':10,'protocols':['https'],
            'authentication':'none','automatic_retries':0,'user_agent':capture_module.USER_AGENT},
        'runtime':{'python':sys.version,'curl_version':curl.stdout.splitlines()[0],
            'pdftotext_version':pdf.stderr.splitlines()[0]},
        'sources':[], 'attempted_sources':0, 'provisionally_usable_sources':0,
        'candidate_annotations':0, 'reference_questions':0,
        'delivery_order_status':'Awaiting separate frozen delivery manifest; publication order was predeclared.'}
    args.manifest.parent.mkdir(parents=True,exist_ok=True)

    def save():
        manifest['sources'].sort(key=lambda x:(ids.index(x['history_id']),x['frame_source_index']))
        manifest['attempted_sources']=len(manifest['sources'])
        manifest['provisionally_usable_sources']=sum(s['content_usability']['provisional_content_usable'] for s in manifest['sources'])
        write_json(args.manifest,manifest)

    save()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures={executor.submit(capture_module.capture,job,fulltext_root):job for job in jobs}
        for future in as_completed(futures):
            result=future.result()
            previous=prior[result['source_id']]
            result['v08_hash_comparison']={
                'prior_manifest_sha256':plan['prior_v08_capture_manifest_sha256'],
                'previous_raw_response_sha256':previous['raw_response_sha256'],
                'previous_extracted_text_sha256':previous['extracted_text_sha256'],
                'raw_hash_matches':result['raw_response_sha256']==previous['raw_response_sha256'],
                'text_hash_matches':result['extracted_text_sha256']==previous['extracted_text_sha256'],
                'interpretation':'Matching bytes do not change the new retrieval clock; differing bytes are new observed versions.'}
            result['page_chrome_policy']='All extracted current text retained, including visible banners/related cards. Separate review flags article versus site chrome; no silent trimming or historical backdating.'
            write_json(Path(result['local_fulltext_directory'])/'capture_receipt.json',result)
            manifest['sources'].append(result)
            save()
            print(json.dumps({'source_id':result['source_id'],'http_status':result['http_status'],
                'raw_bytes':result['raw_bytes'],'text_bytes':result['extracted_text_bytes'],
                'usable':result['content_usability']['provisional_content_usable'],
                'v08_text_matches':result['v08_hash_comparison']['text_hash_matches']}),flush=True)
    manifest['status']='all_selected_sources_attempted'
    manifest['ended_at']=capture_module.now()
    manifest['complete_provisional_histories']=sum(all(s['content_usability']['provisional_content_usable'] for s in manifest['sources'] if s['history_id']==hid) for hid in ids)
    save()
    print(json.dumps({'attempted':manifest['attempted_sources'],'usable':manifest['provisionally_usable_sources'],
        'complete_histories':manifest['complete_provisional_histories'],'manifest_sha256':capture_module.digest(args.manifest)}))


if __name__ == '__main__':
    main()

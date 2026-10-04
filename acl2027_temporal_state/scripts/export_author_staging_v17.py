#!/usr/bin/env python3
"""Physically separate source-only staging projections; does not release authors."""
import argparse
import hashlib
import json
from pathlib import Path
import build_pre_author_bundles_v17 as m


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inventory',required=True)
    ap.add_argument('--external-output',required=True)
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    inventory_path=Path(args.inventory)
    inventory=json.loads(inventory_path.read_text())
    if (inventory.get('schema_version')!='preauthor_bundle_inventory_v17'
        or inventory.get('materialized_bundles')!=21 or len(inventory.get('records',[]))!=21
        or inventory.get('author_release_allowed') is not False):
        raise ValueError('staging_population_or_release_status')
    root=Path(inventory['external_directory']).resolve();out=Path(args.external_output)
    if out.exists() or out.is_symlink() or Path(args.output).exists():raise FileExistsError('output_exists')
    if out.resolve().is_relative_to(m.ROOT.parent):raise ValueError('private_output_in_git')
    prepared=[]
    for record in inventory['records']:
        artifact=record['external_artifact'];name=artifact['filename']
        if Path(name).name!=name:raise ValueError('unsafe_filename')
        path=root/name
        if path.is_symlink() or path.stat().st_size!=artifact['bytes'] or m.digest(path)!=artifact['sha256']:
            raise ValueError('bundle_hash_mismatch')
        value=json.loads(path.read_text())['author_staging']
        expected=m.author_projection(record['bundle_id'],'s'+str(record['source_index']).zfill(2),
                                     record['source_sha256'],value['blocks'])
        if value!=expected:raise ValueError('noncanonical_or_leaking_staging_projection')
        payload=m.encoded(value)
        prepared.append((record['bundle_id']+'.author_staging.json',payload,record['bundle_id']))
    if sum(len(p) for _,p,_ in prepared)>m.MAX_EXTERNAL-65536:raise ValueError('external_cap')
    out.mkdir(parents=True,exist_ok=False)
    artifacts=[]
    for name,payload,bundle_id in prepared:
        with (out/name).open('xb') as stream:stream.write(payload)
        artifacts.append({'bundle_id':bundle_id,'filename':name,'bytes':len(payload),
                          'sha256':hashlib.sha256(payload).hexdigest()})
    result={'schema_version':'source_only_author_staging_export_v17',
      'status':'physically_separated_staging_not_released_or_admitted',
      'inventory_sha256':m.digest(inventory_path),'producer_sha256':m.digest(Path(m.__file__)),
      'exporter_sha256':m.digest(Path(__file__)),'bundles':21,'canonical_allowlist_rechecks':21,
      'external_directory':str(out.resolve()),'external_bytes':sum(a['bytes'] for a in artifacts),
      'artifacts':artifacts,'author_release_allowed':False,'identity_cards':0,
      'questions_authored':0,'references_authored':0,'natural_QA_predictions':0,
      'warning':'Never supply the parent combined bundle file to question authors. These separate projections still await source identity/dependency/semantic admission.'}
    m.write_new(args.output,result)
    print(json.dumps({k:result[k] for k in ('status','bundles','canonical_allowlist_rechecks','external_bytes')}))


if __name__=='__main__':main()

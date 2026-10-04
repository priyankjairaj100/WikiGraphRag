#!/usr/bin/env python3
"""Source-free cross-schema integration check; never opens external artifacts."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_source_occurrence_projection_v19 as producer

ROOT=producer.ROOT
OUTPUT=ROOT/'results/source_view_projection_preflight_v19.json'


def main():
    old_path='results/source_view_availability_summary_v17_2.json'
    new_path='results/source_view_availability_summary_v18_1.json'
    old=producer.read_json(ROOT/old_path); new=producer.read_json(ROOT/new_path)
    lineage=old['original_population_block_lineage']+new['dependency_block_lineage']
    if len(lineage) != 141 or len({(x['source_sha256'],x['block_id']) for x in lineage}) != 141:
        raise producer.ProjectionError('fixed_whole_view_population_mismatch')
    pins=(old_path,new_path,*producer.RASTER_RECEIPTS,*dict.fromkeys(producer.RASTER_RECEIPTS.values()))
    input_bindings={p:producer.digest(ROOT/p) for p in pins}
    records=[]
    for item in lineage:
        admitted=item.get('separate_recovery_receipt') or item.get('admission_receipt') or item.get('original_receipt')
        row={'source_sha256':item['source_sha256'],'block_id':item['block_id'],'status':'failed'}
        try:
            if admitted not in producer.RASTER_RECEIPTS:
                raise producer.ProjectionError('unbound_raster_admission_receipt')
            execution=producer.RASTER_RECEIPTS[admitted]
            view=producer.project_qualified_view(item['source_sha256'],item,'b000',
                 producer.read_json(ROOT/admitted),producer.read_json(ROOT/execution),
                 {'path':admitted,'sha256':input_bindings[admitted]},
                 {'path':execution,'sha256':input_bindings[execution]})
            row.update(status='passed_metadata_join',page_count=len(view['pages']),
                       projected_view_sha256=producer.sha(producer.encoded(view)))
        except producer.ProjectionError as exc:
            row['reason']=str(exc)
        records.append(row)
    failed=sum(r['status'] != 'passed_metadata_join' for r in records)
    result={'schema_version':'source_view_projection_preflight_v19',
            'status':'passed_source_free_metadata_preflight' if not failed else 'failed_source_free_metadata_preflight',
            'script_sha256':producer.digest(__file__),
            'producer_sha256':producer.digest(ROOT/'scripts/build_source_occurrence_projection_v19.py'),
            'input_bindings':input_bindings,'expected_blocks':141,'checked_blocks':len(records),
            'passed_blocks':len(records)-failed,'failed_blocks':failed,'records':records,
            'source_bytes_read':0,'external_artifacts_read':0,'native_calls':0,
            'source_occurrence_packets_materialized':0,'questions_authored':0,
            'scope':'public_receipt_metadata_joins_only_not_actual_artifact_integrity_or_visibility'}
    producer.write_new(OUTPUT,result)
    print({'status':result['status'],'checked_blocks':len(records),'failed_blocks':failed,
           'receipt_sha256':producer.digest(OUTPUT)})
    if failed:sys.exit(1)

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Question-free candidate bundles and all-occurrence registries, never author release."""
from __future__ import annotations
import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from temporal_state.reader_binding_v16 import EvidencePack, BindingError

CODE = ('scripts/build_pre_author_bundles_v17.py', 'tests/test_pre_author_bundles_v17.py',
        'docs/pre_author_bundle_contract_v17.txt', 'src/temporal_state/reader_binding_v16.py')
INPUTS = ('data/reader_binding_v17/bundle_dependency_plan.json',
          'data/reader_binding_v16/table_view_protocol_v16_1.json',
          'results/table_view_inventory_v16_1.json', 'results/reader_pool_v16_1.json',
          'results/source_render_admission_aggregate_v17.json',
          'results/identity_dependency_candidates_v17.json')
MAX_EXTERNAL = 25_000_000


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def inside(inner, outer):
    return outer['byte_start'] <= inner['byte_start'] and inner['byte_stop'] <= outer['byte_stop']


def block_from_view(view, kind, block_id, row_index=None):
    if kind == 'paragraph':
        rows, text, anchor, path = [], view['text'], view['anchor'], view['dom_path']
    elif kind == 'table':
        rows, anchor, path = view['rows'], view['anchor'], view['dom_path']
        text = '\n'.join(row['text'] for row in rows)
    elif kind == 'table_row':
        row = view['rows'][row_index]
        rows, text, anchor = [row], row['text'], row['anchor']
        path = view['dom_path'] + row['path_from_table']
    else:
        raise ValueError('unsupported_block_kind')
    return {'block_id': block_id, 'kind': kind, 'dom_path': path,
            'anchor': deepcopy(anchor), 'text': text, 'rows': deepcopy(rows)}


def clean_rows(rows):
    # Never expose typed links, original raw attributes, or producer-added helper keys.
    return [{'text': row['text'], 'cells': [
        {'text': cell['text'],
         'rowspan': {k:deepcopy(cell['rowspan'][k]) for k in ('declared','parsed','status')},
         'colspan': {k:deepcopy(cell['colspan'][k]) for k in ('declared','parsed','status')}}
        for cell in row['cells']]} for row in rows]


def author_projection(bundle_id, source_id, source_sha256, blocks):
    return {'schema_version': 'source_only_author_staging_v17', 'bundle_id': bundle_id,
            'source_id': source_id, 'source_sha256': source_sha256,
            'author_release_allowed': False, 'identity_card': None,
            'status': 'pending_visible_identity_and_complete_bundle_admission',
            'blocks': [{'block_id': b['block_id'], 'kind': b['kind'], 'dom_path': b['dom_path'],
                        'anchor': {k:b['anchor'][k] for k in ('byte_start','byte_stop','span_sha256')}, 'text': b['text'],
                        'rows': clean_rows(b['rows'])} for b in blocks]}


def collect_facts(facts, blocks, source_sha256):
    selected = {}
    for fact in facts:
        if fact['source_sha256'] != source_sha256:
            raise ValueError('wrong_fact_source')
        owners = [b['block_id'] for b in blocks if inside(fact['anchor'], b['anchor'])]
        if not owners:
            continue
        ordinal = fact['fact_ordinal']
        if ordinal in selected and selected[ordinal]['fact'] != fact:
            raise ValueError('conflicting_duplicate_occurrence')
        selected[ordinal] = {'fact': deepcopy(fact), 'block_ids': owners}
    return [selected[k] for k in sorted(selected)]


def check_dependency(kind, block, facts):
    if kind in ('table', 'table_row') and any(inside(f['anchor'], block['anchor']) for f in facts):
        raise ValueError('dependency_would_import_answer_numeric_occurrences')


def labels_for(fact, blocks, selected_facts):
    labels = []
    for block in blocks:
        for row in block['rows']:
            if not inside(fact['anchor'], row['anchor']):
                continue
            for cell in row['cells']:
                if any(inside(f['fact']['anchor'], cell['anchor']) for f in selected_facts):
                    continue
                label = cell['text'].strip()
                if label and any(c.isalpha() for c in label) and label not in labels:
                    labels.append(label)
    return labels


def candidate_registry(bundle_id, source_id, source_sha256, blocks, selected_facts):
    registry = {'schema_version': 'question_free_candidate_registry_v17', 'bundle_id': bundle_id,
                'status': 'not_admitted_not_for_prediction', 'source_id': source_id,
                'reported_metadata_semantics': 'copied_declared_aspects_not_visible_semantic_certification',
                'population_policy': 'null_until_separate_question_free_source_review', 'facts': []}
    pack = {'schema_version': 'reader_evidence_pack_v16', 'pack_id': bundle_id,
            'blocks': [{'handle': 'b' + str(i).zfill(3), 'text': b['text']} for i,b in enumerate(blocks)],
            'witnesses': [], 'facts': []}
    for i, item in enumerate(selected_facts):
        fact = item['fact']
        handle = 'f' + str(i).zfill(3)
        labels = labels_for(fact, blocks, selected_facts)
        bindings = dict.fromkeys(('concept','entity','period','unit','population','source'))
        aspect_values = dict(fact.get('reported_aspects') or {})
        aspect_values['source'] = {'sha256': source_sha256, 'version': source_id}
        for aspect in ('concept','entity','period','unit','source'):
            value = aspect_values.get(aspect)
            if value is None:
                continue
            if aspect != 'source' and not fact.get('resolved_aspects', {}).get(aspect, False):
                continue
            wh = 'w' + str(i).zfill(3) + '_' + aspect
            witness = {'handle': wh, 'aspect': aspect,
                       'text': 'Declared source metadata (' + aspect + '): ' + json.dumps(value, ensure_ascii=False, sort_keys=True)}
            pack['witnesses'].append(witness)
            bindings[aspect] = {'value': deepcopy(value), 'witnesses': [wh]}
        value = fact.get('normalized_value') if fact.get('numeric_status') == 'normalized' else None
        pack['facts'].append({'handle': handle, 'value': value, 'labels': labels, 'bindings': bindings})
        registry['facts'].append({'handle': handle, 'source_fact_ordinal': fact['fact_ordinal'],
                                  'anchor': deepcopy(fact['anchor']), 'block_ids': item['block_ids'],
                                  'numeric_status': fact.get('numeric_status'), 'binding_status': fact.get('binding_status'),
                                  'lexical_text': fact.get('lexical_text'), 'normalized_value': value,
                                  'declared_aspects': deepcopy(fact.get('reported_aspects')),
                                  'visible_row_label_candidates': labels,
                                  'population_binding': None,
                                  'visible_period_unit_scope_verified': False,
                                  'issues': deepcopy(fact.get('issues', []))})
    gate = {'numeric_occurrences': len(selected_facts), 'blocks': len(blocks),
            'witnesses': len(pack['witnesses']), 'serialized_bytes': len(encoded(pack)),
            'native_tokenization': 'not_attempted', 'population_bindings_complete': False,
            'interface_shape_valid': False, 'reason': None}
    try:
        EvidencePack(pack)
        gate['interface_shape_valid'] = True
    except BindingError as exc:
        gate['reason'] = str(exc)
        pack = None  # The untruncated registry above always retains all occurrences.
    return registry, pack, gate


def freeze(protocol_path, review_path):
    code = {p:digest(ROOT/p) for p in CODE}
    review = json.loads(Path(review_path).read_text())
    if (review.get('status') != 'passed_authored_review' or review.get('open_blockers') != []
        or any(review.get('code_bindings',{}).get(p) != h for p,h in code.items())):
        raise ValueError('independent_review_not_closed_or_mismatched')
    plan = json.loads((ROOT/INPUTS[0]).read_text())
    pool = json.loads((ROOT/INPUTS[3]).read_text())
    expected = [(s['source_path'], t['table_ordinal']) for s in pool['records'] for t in s['selected_table_summaries']]
    if [(b['source_path'],b['table_ordinal']) for b in plan['bundles']] != expected or len(expected) != 21:
        raise ValueError('all_twenty_one_bundle_population_required')
    value = {'schema_version':'preauthor_bundle_materialization_protocol_v17',
             'timing':'after_source_only_dependency_inspection_before_bundle_materialization_or_questions',
             'code_bindings':code,'input_bindings':{p:digest(ROOT/p) for p in INPUTS},
             'review_path':str(Path(review_path).resolve().relative_to(ROOT)), 'review_sha256':digest(review_path),
             'planned_bundles':21,'external_bytes_limit':MAX_EXTERNAL,'author_release_allowed':False}
    write_new(protocol_path,value)


def build(protocol_path, pool_root, view_root, typed_root, external_output, output):
    protocol = json.loads(Path(protocol_path).read_text())
    if protocol.get('schema_version') != 'preauthor_bundle_materialization_protocol_v17':
        raise ValueError('protocol_schema')
    if set(protocol.get('code_bindings',{})) != set(CODE) or set(protocol.get('input_bindings',{})) != set(INPUTS):
        raise ValueError('binding_population')
    for group in ('code_bindings','input_bindings'):
        if any(digest(ROOT/p) != h for p,h in protocol[group].items()):raise ValueError('frozen_input_changed')
    review_path = Path(protocol['review_path'])
    if review_path.is_absolute() or '..' in review_path.parts:raise ValueError('review_path')
    review = json.loads((ROOT/review_path).read_text())
    if (digest(ROOT/review_path)!=protocol['review_sha256'] or review.get('status')!='passed_authored_review'
        or review.get('open_blockers')!=[] or any(review.get('code_bindings',{}).get(p)!=h for p,h in protocol['code_bindings'].items())):
        raise ValueError('review_changed')
    plan, parent, inventory, pool, admission, identity = [json.loads((ROOT/p).read_text()) for p in INPUTS]
    expected = [(s['source_path'],t['table_ordinal']) for s in pool['records'] for t in s['selected_table_summaries']]
    if [(b['source_path'],b['table_ordinal']) for b in plan['bundles']] != expected or len(expected)!=21 or protocol['planned_bundles']!=21:
        raise ValueError('bundle_population_changed')
    roots = [Path(p).resolve() for p in (pool_root,view_root,typed_root)]
    view_by = {s['source_path']:s for s in inventory['records']}
    pool_by = {s['source_path']:s for s in pool['records']}
    for source in parent['sources']:
        name=source['source_path']; art=view_by[name]['external_records']
        checks=[(roots[1],art['filename'],art['bytes'],art['sha256']),
                (roots[2],source['typed_filename'],source['typed_bytes'],source['typed_sha256'])]
        checks += [(roots[0],a['filename'],a['bytes'],a['sha256']) for a in pool_by[name]['external_artifacts'] if a['filename'].endswith('.author.jsonl')]
        for root,filename,size,checksum in checks:
            if Path(filename).name!=filename:raise ValueError('unsafe_basename')
            p=root/filename
            if p.is_symlink() or not p.is_file() or p.stat().st_size!=size or digest(p)!=checksum:raise ValueError('external_input_binding')
    external_output=Path(external_output)
    if external_output.exists() or external_output.is_symlink() or Path(output).exists():raise FileExistsError('attempt_exists')
    if external_output.resolve().is_relative_to(ROOT.parent):raise ValueError('private_output_in_git')
    external_output.mkdir(parents=True,exist_ok=False)
    ad={(b['source_path'],b['block_id']):b for b in admission['block_lineage']}
    records, supplements, total_bytes=[],{},0
    for source_index,source in enumerate(parent['sources']):
        name=source['source_path']; source_id='s'+str(source_index).zfill(2)
        ps=[p for p in plan['bundles'] if p['source_path']==name]
        paragraphs,tables={},{}
        with gzip.open(roots[1]/view_by[name]['external_records']['filename'],'rt') as stream:
            for line in stream:
                r=json.loads(line)
                if r['record_type']=='paragraph':paragraphs[r['paragraph_ordinal']]=r
                elif r['record_type']=='table':tables[r['table_ordinal']]=r
        authors=[a for a in pool_by[name]['external_artifacts'] if a['filename'].endswith('.author.jsonl')]
        author_by={a['table_ordinal']:a for a in map(json.loads,(roots[0]/authors[0]['filename']).read_text().splitlines())}
        facts=[]
        with (roots[2]/source['typed_filename']).open() as stream:
            for line in stream:
                r=json.loads(line)
                if r['record_type']=='fact_pair':facts.append(r['typed'])
        if len(facts)!=source['expected_nonfraction_count'] or [f['fact_ordinal'] for f in facts]!=list(range(len(facts))):
            raise ValueError('complete_typed_occurrence_population_required')
        for p in ps:
            ordinal=p['table_ordinal']; a=author_by[ordinal]
            bundle_id='p'+str(source_index).zfill(2)+'t'+str(ordinal)
            table=block_from_view(tables[ordinal],'table','table_'+str(ordinal))
            table['text']=a['whole_dom_table_text']
            blocks={table['block_id']:table}
            base_ids=[table['block_id']]
            for n in a['neighbor_paragraphs']:
                bid='paragraph_'+str(n['paragraph_ordinal'])
                blocks[bid]=block_from_view(n,'paragraph',bid);base_ids.append(bid)
            for dep in p['additional_dependencies']:
                kind,n=dep['kind'],dep['ordinal']
                if kind=='paragraph':view=paragraphs[n];bid='paragraph_'+str(n)
                else:view=tables[n];bid='table_'+str(n)+(('_row_'+str(dep['row_index'])) if kind=='table_row' else '')
                b=block_from_view(view,kind,bid,dep.get('row_index'))
                check_dependency(kind, b, facts)
                blocks[bid]=b
                key=(name,bid)
                if key not in supplements:supplements[key]={'source_index':source_index,'source_path':name,
                    'source_sha256':source['expected_sha256'],'external_filename':source['external_filename'],
                    'source_bytes':source['expected_bytes'],'block_id':bid,'kind':kind,'dom_path':b['dom_path'],
                    'anchor':b['anchor'],'roles':[],'candidate_bundles':[]}
                if dep['role'] not in supplements[key]['roles']:supplements[key]['roles'].append(dep['role'])
                supplements[key]['candidate_bundles'].append(bundle_id)
            blocks=sorted(blocks.values(),key=lambda b:b['anchor']['byte_start'])
            selected=collect_facts(facts,blocks,source['expected_sha256'])
            registry,pack,gates=candidate_registry(bundle_id,source_id,source['expected_sha256'],blocks,selected)
            private={'author_staging':author_projection(bundle_id,source_id,source['expected_sha256'],blocks),
                     'question_free_registry':registry,'interface_shape_candidate':pack,'gates':gates,
                     'question_or_reference':None,'source_only_dependency_plan':p}
            payload=encoded(private)
            if total_bytes+len(payload)>MAX_EXTERNAL-65_536:raise ValueError('external_cap_no_truncation')
            filename=bundle_id+'.bundle.json'
            with (external_output/filename).open('xb') as stream:stream.write(payload)
            total_bytes+=len(payload)
            base_decisions=[{'block_id':bid,'decision':ad.get((name,bid),{}).get('derived_decision','missing_admission')} for bid in base_ids]
            records.append({'bundle_id':bundle_id,'source_index':source_index,'source_path':name,
                'source_sha256':source['expected_sha256'],'selected_table_ordinal':ordinal,
                'block_count':len(blocks),'base_view_decisions':base_decisions,'additional_dependencies':[
                    {k:v for k,v in b.items() if k in ('block_id','kind','dom_path','anchor')} for b in blocks if b['block_id'] not in base_ids],
                'numeric_occurrences':len(selected),'numeric_status_counts':{s:sum(f['fact'].get('numeric_status')==s for f in selected) for s in sorted({f['fact'].get('numeric_status') for f in selected})},
                'interface_candidate_gates':gates,'author_release_allowed':False,'complete_evidence_admitted':False,
                'population_mappings_verified':0,'unresolved':p['unresolved'],
                'semantic_support_candidates':p.get('semantic_support_candidates',[]),
                'unsupported_until_review':p.get('unsupported_until_review',[]),
                'next_semantic_action':p.get('next_semantic_action'),
                'external_artifact':{'filename':filename,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}})
    result={'schema_version':'preauthor_bundle_inventory_v17','status':'materialized_candidates_not_admitted',
            'protocol_sha256':digest(protocol_path),'planned_bundles':21,'materialized_bundles':len(records),
            'records':records,'additional_dependency_block_count':len(supplements),
            'additional_dependency_blocks':list(supplements.values()),'external_directory':str(external_output.resolve()),
            'external_bytes':total_bytes,'author_release_allowed':False,'questions_authored':0,'references_authored':0,
            'natural_QA_predictions':0,'complete_evidence_admitted':0,
            'limits':'Shape-valid packs remain unadmitted; population null, no native tokenization, no identity-card release.'}
    write_new(output,result)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze');f.add_argument('--protocol',required=True);f.add_argument('--review',required=True)
    b=sub.add_parser('build')
    for arg in ('protocol','pool-root','view-root','typed-root','external-output','output'):b.add_argument('--'+arg,required=True)
    a=ap.parse_args()
    if a.command=='freeze':freeze(a.protocol,a.review);print('Frozen pre-author bundle materialization inputs; no source build.')
    else:
        r=build(a.protocol,a.pool_root,a.view_root,a.typed_root,a.external_output,a.output)
        print(json.dumps({k:r[k] for k in ('status','planned_bundles','materialized_bundles','additional_dependency_block_count','external_bytes')}))

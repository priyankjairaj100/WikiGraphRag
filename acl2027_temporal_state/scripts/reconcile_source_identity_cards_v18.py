#!/usr/bin/env python3
"""Reconcile fixed visible identity candidates; no new visual or semantic review."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from lxml import etree

ROOT=Path(__file__).resolve().parents[1]
CANDIDATES=ROOT/'results/source_identity_card_candidates_v18.json'
CANDIDATE_SHA='14627b497d1f9431b2629128ee8a677a90bdaebaff6c06f197691c41ae8ef89c'
AVAILABILITY=ROOT/'results/source_view_availability_summary_v18.json'
AVAILABILITY_SHA='7fac96825e0836130f862d3e083703f2d9b2a9729a4475e802e930a05c6e057c'
OUTPUT=Path('/dev/shm/wikigraph_v15/external/source_identity_cards_admitted_v18')
RECONCILIATION=ROOT/'results/source_identity_card_reconciliation_v18.json'
ADMISSION=ROOT/'results/source_identity_card_admission_v18.json'
ADMITTED='admitted_as_qualified_inspection_view'
NORMALIZATION='outer_whitespace_only; no name, case, date, year, or legal-form normalization'
FIELDS={'issuer_name','report_type','report_end_wording'}
IDENTITY_FIELDS={'issuer_name':'EntityRegistrantName','report_type':'DocumentType',
                 'report_end_wording':'DocumentPeriodEndDate'}
CARD_KEYS={'author_release','card_admitted','essential_fields','independent_reconciliation_status',
           'native_reader_calls','physical_provenance','questions_authored','reference_answers_authored',
           'registry_updates','reviewer_role','schema_version','scope_limits','source_id',
           'source_view_availability','source_witnesses','status'}
CHANGED_KEYS={'schema_version','status','card_admitted','independent_reconciliation_status',
              'independent_reconciliation_reference'}


class ReconciliationError(ValueError):pass


def require(condition,code):
    if not condition:raise ReconciliationError(code)


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(path):return sha(Path(path).read_bytes())
def binding(path):
    p=Path(path);return {'path':str(p),'bytes':p.stat().st_size,'sha256':digest(p)}


def read_bound(ref):
    require(set(ref)=={'path','bytes','sha256'},'artifact_binding_shape')
    p=Path(ref['path'])
    require(p.is_absolute() and p.is_file() and not p.is_symlink(),'artifact_file_missing_or_symlink')
    raw=p.read_bytes()
    require(len(raw)==ref['bytes'] and sha(raw)==ref['sha256'],'artifact_hash_or_bytes_mismatch')
    return raw


def read_json(path):return json.loads(Path(path).read_text())
def write_new(path,obj):
    with Path(path).open('x',encoding='utf-8') as f:
        json.dump(obj,f,indent=2,ensure_ascii=False,sort_keys=True);f.write('\n')


def public_relative(ref):
    try:return str(Path(ref['path']).relative_to(ROOT))
    except ValueError:raise ReconciliationError('admission_receipt_not_in_project')


def source_block(document,index,block_id):
    sources=[s for s in document['sources'] if s.get('original_source_index',s['source_index'])==index]
    require(len(sources)==1,'source_index_not_unique')
    blocks=[b for b in sources[0]['blocks'] if b['block_id']==block_id]
    require(len(blocks)==1,'block_not_unique')
    return sources[0],blocks[0]


def admitted_artifacts(block):
    if 'reviewed_artifacts' in block:
        rows=block['reviewed_artifacts']
        require(len({r['filename'] for r in rows})==len(rows),'duplicate_reviewed_artifact')
        return {r['filename']:{'bytes':r['bytes'],'sha256':r['sha256']} for r in rows}
    result={k:{'bytes':v['bytes'],'sha256':v['sha256']} for k,v in block['artifact_bindings'].items()}
    for i,r in enumerate(block['raster_bindings']):
        result['raster/page-'+str(i+1)+'.png']={'bytes':r['bytes'],'sha256':r['sha256']}
    return result


def body_text(raw):
    parser=etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,recover=False)
    tree=etree.fromstring(raw,parser)
    bodies=tree.xpath('//*[local-name()="body"]')
    require(len(bodies)==1,'source_assembly_body_not_unique')
    return ''.join(bodies[0].itertext())


def reconcile_fields(card):
    require(set(card)==CARD_KEYS,'candidate_extra_or_missing_fields')
    require(card['schema_version']=='visible_source_identity_card_candidate_v18','candidate_schema')
    require(card['status']=='candidate_pending_independent_reconciliation'
            and card['independent_reconciliation_status']=='pending','candidate_status')
    require(card['card_admitted'] is False and card['author_release'] is False,'candidate_release_state')
    require(all(card[k]==0 for k in ('native_reader_calls','questions_authored','reference_answers_authored','registry_updates')),
            'candidate_nonidentity_activity')
    require(set(card['essential_fields'])==FIELDS,'optional_or_missing_identity_field')
    witnesses={w['witness_id']:w for w in card['source_witnesses']}
    require(len(witnesses)==len(card['source_witnesses']),'duplicate_witness_id')
    used=[]
    for name,field in card['essential_fields'].items():
        keys={'literal','normalization','witness_ids'}|({'supporting_witness_ids'} if name=='issuer_name' else set())
        require(set(field)==keys and field['normalization']==NORMALIZATION,'field_shape_or_normalization')
        require(isinstance(field['literal'],str) and bool(field['literal']),'empty_literal')
        require(len(field['witness_ids'])==1,'field_requires_one_whole_witness')
        wid=field['witness_ids'][0];require(wid in witnesses,'missing_field_witness')
        witness=witnesses[wid]
        require(witness['semantic_role']==name,'field_witness_role_mismatch')
        require(field['literal']==witness['whole_visible_source_text'].strip(),'field_not_full_literal_outer_whitespace_only')
        used.append(wid)
        support=field.get('supporting_witness_ids',[])
        require(len(support)==(1 if name=='issuer_name' and card['source_id'] in ('s10','s11') else 0),'unexpected_support_population')
        for sid in support:
            require(sid in witnesses and witnesses[sid]['semantic_role']=='issuer_name_caption','support_role_mismatch')
            used.append(sid)
    require(len(used)==len(set(used)) and set(used)==set(witnesses),'unused_or_aliased_witness')
    require(card['physical_provenance']['source_id']==card['source_id'],'physical_source_id_mismatch')


def admitted_copy(card,reference):
    new=deepcopy(card)
    new.update(schema_version='visible_source_identity_card_admitted_v18',
               status='literal_cover_identity_admitted_not_author_released',card_admitted=True,
               independent_reconciliation_status='passed_mechanical_reconciliation',
               independent_reconciliation_reference=deepcopy(reference))
    require(set(new)-set(card)=={'independent_reconciliation_reference'},'unexpected_admitted_field')
    require(canonical({k:v for k,v in new.items() if k not in CHANGED_KEYS})==
            canonical({k:v for k,v in card.items() if k not in CHANGED_KEYS}),'candidate_content_changed')
    require(new['author_release'] is False,'author_release_forbidden')
    return new


def reconcile():
    require(not OUTPUT.exists() and not OUTPUT.is_symlink() and not RECONCILIATION.exists()
            and not ADMISSION.exists(),'new_destination_required')
    rows=[];cards=[];witness_rows=[];current={}
    try:
        require(digest(CANDIDATES)==CANDIDATE_SHA and digest(AVAILABILITY)==AVAILABILITY_SHA,'fixed_input_hash_changed')
        candidates=read_json(CANDIDATES);availability=read_json(AVAILABILITY)
        require(candidates['source_view_availability_binding']==binding(AVAILABILITY),'candidate_availability_binding')
        require(candidates['planned_sources']==candidates['candidates_constructed']==12
                and candidates['essential_fields_constructed']==36 and candidates['cards_admitted']==0
                and candidates['author_release'] is False,'candidate_denominator_or_release')
        require([r['source_id'] for r in candidates['records']]==['s'+str(i).zfill(2) for i in range(12)],'fixed_twelve_source_order')
        public_pins=dict(availability['input_bindings'])
        previous=read_json(ROOT/'results/source_view_availability_summary_v17_2.json')
        require(digest(ROOT/'results/source_view_availability_summary_v17_2.json')==public_pins['results/source_view_availability_summary_v17_2.json'],'previous_aggregate_binding')
        public_pins.update(previous['input_bindings'])
        for path,checksum in public_pins.items():require(digest(ROOT/path)==checksum,'bound_public_input_changed')
        json_cache={}
        def bound_json(ref):
            raw=read_bound(ref)
            key=(ref['path'],ref['sha256'])
            if key not in json_cache:json_cache[key]=json.loads(raw)
            return json_cache[key]
        lineage={(r['source_index'],r['block_id']):r for r in availability['identity_block_lineage']}
        counts={'original_identity':0,'nvda_recovery':0,'slb_literal_supplement':0}
        for index,row in enumerate(candidates['records']):
            current={'source_id':row['source_id']}
            card=json.loads(read_bound(row['candidate']));reconcile_fields(card)
            require(card['source_id']==row['source_id'],'card_source_id_mismatch')
            require(card['source_view_availability']==binding(AVAILABILITY),'card_availability_binding')
            source_sha=card['physical_provenance']['frozen_source_sha256']
            for witness in card['source_witnesses']:
                current['block_id']=witness['block_id']
                receipt_ref=witness['qualified_view_admission_receipt'];receipt_path=public_relative(receipt_ref)
                require(public_pins.get(receipt_path)==receipt_ref['sha256'],'admission_not_bound_by_aggregate')
                receipt=bound_json(receipt_ref);review_index=bound_json(witness['review_index'])
                source,reviewed=source_block(receipt,index,witness['block_id'])
                indexed_source,indexed=source_block(review_index,index,witness['block_id'])
                require(source['source_sha256']==indexed_source['source_sha256']==source_sha==witness['physical_source_sha256'],
                        'physical_source_hash_mismatch')
                require(source['source_path']==indexed_source['source_path'],'physical_source_path_mismatch')
                require(reviewed['decision']==witness['qualified_view_status']==ADMITTED,'view_not_admitted')
                require(reviewed['anchor']==indexed['anchor']==witness['anchor']
                        and reviewed['dom_path']==indexed['dom_path']==witness['dom_path'],'source_locator_mismatch')
                role=witness['semantic_role']
                if indexed.get('role')=='identity_dependency_candidate':
                    require(role in IDENTITY_FIELDS and IDENTITY_FIELDS[role] in indexed['identity_fields'],'identity_field_role_mismatch')
                    line=lineage[(index,witness['block_id'])]
                    require(line['derived_available_decision']==ADMITTED and line['anchor']==witness['anchor']
                            and line['source_sha256']==source_sha
                            and (line.get('separate_recovery_receipt') or line['receipt'])==receipt_path,'aggregate_identity_lineage_mismatch')
                    counts['nvda_recovery' if line.get('separate_recovery_receipt') else 'original_identity']+=1
                else:
                    require(index in (10,11) and ((role=='issuer_name' and indexed.get('role')=='literal_cover_issuer_name')
                            or (role=='issuer_name_caption' and indexed.get('role')=='adjacent_charter_name_label')),'literal_supplement_role_mismatch')
                    require(receipt_path=='results/source_render_slb_cover_admission_root_v17_3.json','literal_supplement_receipt_mismatch')
                    counts['slb_literal_supplement']+=1
                require(witness['actual_raster_pages']==indexed['pngs'],'raster_index_binding_mismatch')
                page_indices=list(range(len(indexed['pngs'])))
                require(page_indices and page_indices==witness['actual_raster_pages_inspected']==reviewed['raster_page_indices_inspected']
                        and reviewed['raster_pages_available']==len(page_indices),'prior_all_page_review_not_recorded')
                artifacts=admitted_artifacts(reviewed)
                named=[('source-assembly.html',witness['original_source_assembly']),
                       ('source-fragment.xml',witness['source_fragment'])]
                named += [('raster/page-'+str(i+1)+'.png',ref) for i,ref in enumerate(witness['actual_raster_pages'])]
                for name,ref in named:
                    raw=read_bound(ref)
                    require(artifacts.get(name)=={'bytes':ref['bytes'],'sha256':ref['sha256']},'artifact_not_in_admitted_receipt')
                    if not name.startswith('raster/'):
                        require(ref==indexed['artifacts'][name],'source_artifact_index_mismatch')
                    if name=='source-assembly.html':
                        require(body_text(raw)==witness['whole_visible_source_text'],'whole_source_body_text_mismatch')
                    if name=='source-fragment.xml':
                        anchor=witness['anchor']
                        require(len(raw)==anchor['byte_stop']-anchor['byte_start'] and sha(raw)==anchor['span_sha256'],'source_span_digest_mismatch')
                require(sha(witness['whole_visible_source_text'].encode())==witness['visible_text_sha256'],'visible_text_digest_mismatch')
                witness_rows.append({'source_id':card['source_id'],'block_id':witness['block_id'],
                    'source_sha256':source_sha,'semantic_role':role,'admission_receipt':receipt_path,
                    'source_locator_reconciled':True,'source_body_text_exact':True,'artifact_digests_reconciled':True,
                    'prior_reviewed_pages_reconciled':len(page_indices)})
            cards.append(card);rows.append({'source_id':card['source_id'],'candidate':row['candidate'],
                'literal_fields_reconciled':3,'witness_blocks_reconciled':len(card['source_witnesses'])})
        require(len(cards)==12 and len(witness_rows)==38 and counts=={'original_identity':33,'nvda_recovery':1,'slb_literal_supplement':4},'terminal_population_mismatch')
        proof={'schema_version':'visible_source_identity_reconciliation_v18','status':'all_12_candidates_pass_mechanical_reconciliation',
               'script':binding(__file__),'candidate_receipt':binding(CANDIDATES),'source_view_availability':binding(AVAILABILITY),
               'authored_controls':{'file':binding(ROOT/'tests/test_source_identity_reconciliation_v18.py'),
                   'command':"PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_source_identity_reconciliation_v18.py' -v",
                   'tests_run_before_reconciliation':9,'passed':9,'failures':0,'errors':0},
               'planned_cards':12,'literal_fields_reconciled':36,'witness_blocks_reconciled':38,'view_lineage_counts':counts,
               'records':rows,'witness_checks':witness_rows,'new_visual_pages_inspected':0,
               'review_roles':{'prior_visual_review':'Bound per-block admitted source/CSS/raster receipts and candidate owner actual38-page inspection.',
                   'prior_root_literal_review':'Root reported independently reading36 fields and extracting38 XML body itertexts before assigning this reconciliation.',
                   'this_review':'Independent mechanical digest/locator/role/literal/projection reconciliation; no additional visual or semantic annotation.'},
               'independent_human_annotation':False,'inter_annotator_agreement_claimed':False,
               'table_entity_period_population_binding_admitted':False,'author_release':False}
        write_new(RECONCILIATION,proof)
        reference=binding(RECONCILIATION)
        admitted=[admitted_copy(card,reference) for card in cards]
        payloads=[(canonical(card)+'\n').encode() for card in admitted]
        require(sum(map(len,payloads))<=1_000_000,'identity_output_byte_cap')
        OUTPUT.mkdir(parents=True,exist_ok=False)
        outputs=[]
        for card,raw in zip(admitted,payloads):
            p=OUTPUT/(card['source_id']+'.identity_admitted.json')
            with p.open('xb') as f:f.write(raw)
            outputs.append({'source_id':card['source_id'],'admitted_card':binding(p),'card_admitted':True,'author_release':False})
        for row in candidates['records']:read_bound(row['candidate'])
        require(digest(CANDIDATES)==CANDIDATE_SHA and digest(AVAILABILITY)==AVAILABILITY_SHA,'parent_changed_during_reconciliation')
        result={'schema_version':'visible_source_identity_card_admission_v18','status':'12_literal_cover_identity_cards_admitted_not_author_released',
                'reconciliation':reference,'candidate_receipt':binding(CANDIDATES),'source_view_availability':binding(AVAILABILITY),
                'script':binding(__file__),'planned_cards':12,'cards_admitted':12,'literal_fields_admitted':36,
                'whole_source_witnesses_reconciled':38,'records':outputs,'external_directory':str(OUTPUT),
                'external_bytes':sum(map(len,payloads)),'candidate_files_unchanged':True,
                'allowed_changed_keys':sorted(CHANGED_KEYS),'all_other_card_content_exact':True,'author_release':False,
                'new_visual_pages_inspected':0,'model_calls':0,'questions_authored':0,'reference_answers_authored':0,
                'registry_updates':0,'semantic_packs_admitted':0,'table_entity_period_unit_population_bindings_admitted':0,
                'optional_fields_added':0,'legal_entity_alias_or_taxonomy_inference':False,
                'limitations':['Admitted literal cover identity only; does not establish financial table scopes or all fact periods.',
                               'Physical hashes are version provenance, not SEC-byte equivalence or independent legal-entity verification.',
                               'Prior actual visual review, root literal reading and this mechanical reconciliation are distinct; no IAA or human-gold claim.',
                               'Cards remain unavailable to question authors until complete packet admission and release.']}
        write_new(ADMISSION,result)
        return result
    except Exception as error:
        failure={'schema_version':'visible_source_identity_card_admission_v18','status':'reconciliation_failed_no_release',
                 'planned_cards':12,'cards_admitted':0,'author_release':False,'cursor':current,
                 'completed_candidate_checks':len(rows),'completed_witness_checks':len(witness_rows),
                 'failure_code':str(error) if isinstance(error,ReconciliationError) else type(error).__name__,
                 'partial_output_directory_preserved':str(OUTPUT) if OUTPUT.exists() else None,
                 'candidate_receipt_expected_sha256':CANDIDATE_SHA,'source_view_expected_sha256':AVAILABILITY_SHA,
                 'script':binding(__file__),'no_automatic_retry':True,'questions_authored':0,'model_calls':0}
        write_new(ADMISSION,failure)
        return failure


if __name__=='__main__':
    result=reconcile()
    print(json.dumps({k:result[k] for k in ('status','planned_cards','cards_admitted','author_release')}))

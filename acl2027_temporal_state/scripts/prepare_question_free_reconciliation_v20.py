"""Bounded private provenance adapter, with the producer's pure anchor-key helper.

This does not implement semantic mapping, review decisions, or registry output.
Call prepare(anchor_key, helper_reference) only with the agreed pure helper.
"""
import collections,datetime,hashlib,json
from pathlib import Path

OUT=None
RESEARCH=Path(__file__).resolve().parents[1]
INITIAL=Path('/dev/shm/wikigraph_v15/external/source_occurrence_semantics_v19/initial_freeze_manifest.json')
TYPED_INVENTORY=RESEARCH/'results/pre_author_bundle_inventory_v18.json'
PLAN=RESEARCH/'docs/question_free_reconciliation_preparation_plan_v20.txt'
BRIDGE=RESEARCH/'docs/question_free_registry_bridge_plan_v19.txt'
ASPECTS=('concept','entity','period','unit','population','source')
sha=lambda b:hashlib.sha256(b).hexdigest()
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def ref(p):return dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p.read_bytes()))
def load(p):return json.loads(p.read_text())
def bound(r):
 p=Path(r['path']);assert p.stat().st_size==r['bytes'] and sha(p.read_bytes())==r['sha256'];return load(p)
def write(p,x):
 assert not p.exists(),p
 p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')

def prepare(anchor_key,helper_reference,*,output_directory,public_receipt):
 global OUT
 OUT=Path(output_directory).resolve()
 OUT.mkdir(parents=True,exist_ok=True)
 assert sha(INITIAL.read_bytes())=='0d413a681ca19f188909fc3c81dda398fa073d58a987251bb12b9eab47b39dd2'
 assert sha(TYPED_INVENTORY.read_bytes())=='b109df88b8c0180be72a5931a3120e71a9a80b104218b168bfe09e1594114257'
 assert sha(PLAN.read_bytes())=='88056f33f4514b2e56329fd76330efc67b874a751e4d904aaaf0118a70680181'
 frozen=load(INITIAL);inventory=load(TYPED_INVENTORY);typed_records={r['bundle_id']:r for r in inventory['records']}
 records=[];source_states=collections.Counter();presentations=collections.Counter();dispositions=collections.Counter();joins=collections.Counter();categories=collections.Counter();input_refs=[];keys=[]
 for parent in frozen['records']:
  bundle=parent['bundle_handle'];ledger=bound(parent['initial_ledger']);projection=bound(parent['projection']);ir=typed_records.pop(bundle)
  typed_path=Path(inventory['external_directory'])/ir['external_artifact']['filename']
  tr=ref(typed_path);assert tr['sha256']==ir['external_artifact']['sha256'] and tr['bytes']==ir['external_artifact']['bytes']
  registry=load(typed_path)['question_free_registry'];facts=registry['facts']
  assert ledger['status']=='initial_review_frozen_pending_reconciliation' and ledger['author_release_allowed'] is False
  assert len(ledger['entries'])==len(projection['occurrences'])==parent['entry_count']
  index=collections.defaultdict(list);invalid_fact_anchors=[]
  for i,fact in enumerate(facts):
   try:k=anchor_key(fact['anchor'])
   except (ValueError,TypeError,KeyError):invalid_fact_anchors.append(i);continue
   index[k].append(i)
  out_entries=[];used=set();bundle_join_counts=collections.Counter();bundle_unknown_entries=0
  for i,(entry,occ) in enumerate(zip(ledger['entries'],projection['occurrences'])):
   expected=dict(bundle_handle=bundle,occurrence_handle=occ['occurrence_handle'],entry_sha256=occ['entry_sha256'])
   assert entry['key']==expected;keys.append(expected)
   locator=occ.get('locator');identity=None;reason=None;candidate_indexes=[]
   try:
    identity=dict(source_sha256=projection['source_sha256'],**locator['anchor'],dom_path=locator['dom_path'])
    candidate_indexes=index.get(anchor_key(identity),[])
    state='exact_unique' if len(candidate_indexes)==1 else ('unmatched' if not candidate_indexes else 'ambiguous')
   except (ValueError,TypeError,KeyError):state='provenance_mismatch';reason='projection_locator_not_valid_for_exact_anchor_key'
   candidates=[dict(typed_fact_index=j,typed_fact_handle=facts[j]['handle'],typed_record_sha256=sha(canonical(facts[j]))) for j in candidate_indexes]
   selected=candidate_indexes[0] if state=='exact_unique' else None
   original=facts[selected] if selected is not None else None
   if selected is not None:used.add(selected)
   counts=collections.Counter(a['state'] for a in entry['attachments']);source_states.update(counts)
   has_unknown=any(a['state'] in ('ambiguous','not_stated_in_fixed_projection','unreviewable') for a in entry['attachments'])
   uncertain_presentation=entry['presentation']['status'] in ('ambiguous','unresolved_locator','not_visibly_exposed')
   bundle_unknown_entries+=int(has_unknown);categories['entries_with_source_unknown_relations']+=int(has_unknown);categories['entries_with_uncertain_presentation']+=int(uncertain_presentation)
   source_by_aspect=collections.defaultdict(collections.Counter)
   for a in entry['attachments']:source_by_aspect[a['aspect']][a['state']]+=1
   declared=original['declared_aspects'] if original is not None else {}
   pending=[]
   for aspect in ASPECTS:
    present=(declared.get(aspect) is not None) if aspect not in ('population','source') else (original is not None and (original.get('population_binding') is not None if aspect=='population' else all(original['anchor'].get(k) is not None for k in ('source_sha256','source_version'))))
    pending.append(dict(aspect=aspect,reconciliation_state='pending_separate_review',accepted_value=None,declared_binding_present=bool(present),source_to_declared_correspondence='not_established'))
   proposed=dict(entry_key=expected,frozen_input_refs=dict(ledger=parent['initial_ledger'],projection=parent['projection'],typed_bundle=tr,ledger_entry_index=i),source_identity=identity,join=dict(state=state,reason=reason,typed_fact_index=selected,typed_fact_handle=original['handle'] if original is not None else None,candidate_refs=candidates),unchanged_typed_record=original,unchanged_typed_record_sha256=sha(canonical(original)) if original is not None else None,source_review=dict(disposition=entry['disposition'],presentation=entry['presentation'],attachments=entry['attachments'],unknown_reason_codes=entry['unknown_reason_codes'],witness_registry_ref=parent['initial_ledger']),preparation_by_aspect=pending,coverage_categories=dict(source_review_entry_accounted_for=True,source_disposition=entry['disposition'],source_presentation=entry['presentation']['status'],source_relation_state_counts=dict(counts),source_relation_states_by_aspect={k:dict(v) for k,v in source_by_aspect.items()},source_unknown_relations_retained=has_unknown,uncertain_presentation_retained=uncertain_presentation,join_candidate_count=len(candidate_indexes),all_six_correspondences_pending=True))
   assert proposed['source_review']['attachments']==entry['attachments']
   if original is not None:assert canonical(proposed['unchanged_typed_record'])==canonical(facts[selected])
   assert all(a['accepted_value'] is None and a['reconciliation_state']=='pending_separate_review' for a in pending)
   out_entries.append(proposed);joins[state]+=1;bundle_join_counts[state]+=1;dispositions[entry['disposition']]+=1;presentations[entry['presentation']['status']]+=1
  artifact=dict(schema_version='question_free_reconciliation_preparation_bundle_v20',bundle_handle=bundle,status='proposed_exact_anchor_inventory_pending_separate_review',role='privileged_question_free_reconciliation_preparation',author_role=False,author_release_allowed=False,registry_edits=0,aspect_support_certifications=0,source_ledger=parent['initial_ledger'],source_projection=parent['projection'],typed_bundle=tr,entry_count=len(out_entries),typed_registry_entry_count=len(facts),exact_typed_record_coverage_count=len(used),unmatched_typed_fact_indexes=[j for j in range(len(facts)) if j not in used],invalid_typed_anchor_indexes=invalid_fact_anchors,entries=out_entries)
  path=OUT/(bundle+'.preparation.json');write(path,artifact)
  records.append(dict(bundle_handle=bundle,entry_count=len(out_entries),typed_registry_entry_count=len(facts),join_state_counts=dict(bundle_join_counts),entries_with_source_unknown_relations=bundle_unknown_entries,all_six_correspondences_pending=True,matched_typed_entry_count=len(used),unmatched_typed_entry_count=len(facts)-len(used),invalid_typed_anchor_count=len(invalid_fact_anchors),artifact=ref(path),source_ledger=parent['initial_ledger'],source_projection=parent['projection'],typed_bundle=tr))
  input_refs.append(dict(bundle_handle=bundle,ledger=parent['initial_ledger'],projection=parent['projection'],typed_bundle=tr))
 assert not typed_records and len(keys)==326 and len(records)==21
 assert len({(k['bundle_handle'],k['occurrence_handle'],k['entry_sha256']) for k in keys})==326
 transition=dict(schema_version='question_free_reconciliation_role_transition_v20',recorded_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),role='/root/semantic_bundle_v18',current_role='privileged_question_free_reconciliation_preparation',prior_role='source_only_initial_annotation_coordinator',role_transition_explicitly_authorized=True,transition_order='The initial v19 ledgers were frozen before root authorized access to the v18 typed question-free registry in this separate v20 task. The role transition was announced before the first typed inventory/registry structure read.',prior_source_only_initial_freeze=ref(INITIAL),prior_public_receipt=ref(RESEARCH/'results/source_occurrence_semantic_initial_receipt_v19.json'),new_access_scope='Frozen initial ledgers, frozen source-only occurrence packets, and exact v18 question-free typed registry objects reached by bound inventory; no natural questions, reference answers, predictions, or native outputs.',typed_registry_inventory=ref(TYPED_INVENTORY),preparation_plan=ref(PLAN),bridge_plan=ref(BRIDGE),actual_input_refs=input_refs,pure_anchor_helper=helper_reference,adapter=ref(Path(__file__)),independent_source_only_claim_for_v20=False,independent_human_annotation=False,question_author_role=False,natural_question_reads=0,reference_answer_reads=0,prediction_reads=0,native_output_reads=0,source_searches=0,source_expansion=0,prior_artifact_mutations=0,registry_edits=0,source_or_typed_support_certifications=0,author_release_allowed=False)
 write(OUT/'role_transition_and_exposure.json',transition)
 coverage=dict(schema_version='question_free_reconciliation_preparation_coverage_v20',status='complete_proposed_inventory_pending_separate_review',fixed_bundle_count=21,processed_bundle_count=len(records),fixed_entry_count=326,accounted_entry_count=len(keys),typed_registry_entry_count=sum(r['typed_registry_entry_count'] for r in records),join_state_counts=dict(joins),source_disposition_counts=dict(dispositions),source_presentation_counts=dict(presentations),source_relation_state_counts=dict(source_states),coverage_categories=dict(categories),pending_correspondence_count=len(keys)*len(ASPECTS),accepted_correspondence_count=0,exact_ordered_entry_keys_retained=True,every_retained_entry_once=True,source_annotations_unchanged=True,typed_records_unchanged=True,value_or_label_joining=False,all_unknowns_retained=True,registry_edits=0,admissions_created=0,author_release_allowed=False,records=records)
 write(OUT/'coverage.json',coverage)
 manifest=dict(schema_version='question_free_reconciliation_preparation_manifest_v20',status=coverage['status'],preparation_plan=ref(PLAN),bridge_plan=ref(BRIDGE),initial_freeze=ref(INITIAL),typed_registry_inventory=ref(TYPED_INVENTORY),role_transition=ref(OUT/'role_transition_and_exposure.json'),pure_anchor_helper=helper_reference,adapter=ref(Path(__file__)),coverage=ref(OUT/'coverage.json'),records=records,accepted_correspondences=0,registry_edits=0,author_release_allowed=False)
 write(OUT/'preparation_manifest.json',manifest)
 receipt={k:v for k,v in coverage.items() if k!='records'}
 receipt.update(schema_version='question_free_reconciliation_preparation_receipt_v20',preparation_plan=ref(PLAN),initial_freeze=ref(INITIAL),typed_registry_inventory=ref(TYPED_INVENTORY),private_manifest=ref(OUT/'preparation_manifest.json'),role_transition=ref(OUT/'role_transition_and_exposure.json'),pure_anchor_helper=helper_reference,records=records,methodological_scope='Exact source-identity joins are proposed physical correspondences only. All six aspect decisions remain pending with null accepted values. Initial visible annotations and copied declared metadata stay distinct; source unknowns do not automatically reject a bundle. No registry overlay, supported review decisions, author packet, admission, or release is emitted.',exposure_scope='The coordinating source-only model role explicitly transitioned to privileged reconciliation preparation after all initial ledgers froze; it is not a question author or independent human reviewer.')
 public=Path(public_receipt).resolve();write(public,receipt)
 print(json.dumps({k:v for k,v in coverage.items() if k not in ('records','source_relation_state_counts')},indent=2));print('public_receipt',json.dumps(ref(public)));print('private_manifest',json.dumps(ref(OUT/'preparation_manifest.json')))


def main():
 import argparse,importlib.util,shutil,sys
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--output-directory',required=True,help='New private attempt directory; existing output artifacts are never overwritten.')
 parser.add_argument('--public-receipt',required=True,help='New source-free metadata receipt path; existing receipts are never overwritten.')
 args=parser.parse_args()
 out=Path(args.output_directory).resolve();out.mkdir(parents=True,exist_ok=True)
 source=RESEARCH/'src/temporal_state/question_free_registry_bridge_v20.py'
 snapshot=out/'pure_join_module_snapshot.py'
 assert not snapshot.exists(),snapshot
 shutil.copyfile(source,snapshot)
 sys.path.insert(0,str(RESEARCH/'src'))
 spec=importlib.util.spec_from_file_location('temporal_state._reconciliation_preparation_snapshot_v20',snapshot)
 helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
 helper_ref=ref(snapshot)
 helper_ref.update(original_module_path=str(source),function='anchor_identity',use='exact_source_anchor_identity_only',full_decision_engine_called=False,dependency=ref(RESEARCH/'src/temporal_state/reader_binding_v18.py'))
 prepare(helper.anchor_identity,helper_ref,output_directory=out,public_receipt=args.public_receipt)

if __name__=='__main__':
 main()

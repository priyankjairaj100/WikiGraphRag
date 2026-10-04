#!/usr/bin/env python3
"""Freeze then apply the reviewed bridge to the fixed corpus; no admission.

prepare loads and hashes immutable inputs but never calls reconcile_bundle.
apply requires the exact root-authorized freeze digest. It writes only private
overlays/sidecars plus a source-free result receipt and preserves failed attempts.
"""
import argparse, collections, hashlib, json, re, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXT=Path('/dev/shm/wikigraph_v15/external')
ENGINE=ROOT/'src/temporal_state/question_free_registry_bridge_v20.py'
ENGINE_SHA='2c74ade40f61b491dc76d01b2319aeb9b3e6fe634cbf3f66b02695d752a0eee6'
REVIEW=ROOT/'results/question_free_registry_bridge_independent_review_v20.json'
REVIEW_SHA='f544301483fdff2cb98f9552ef0b04927a84e9b8747b82d78fa3404090a9c9b2'
PREP=EXT/'question_free_reconciliation_v20/preparation_manifest.json'
PREP_SHA='c5bf5ae6aac5867b95bd617fe0322938e9f6cdb356c2f3e4d921b438a8e9cdd2'
INITIAL=EXT/'source_occurrence_semantics_v19/initial_freeze_manifest.json'
INITIAL_SHA='0d413a681ca19f188909fc3c81dda398fa073d58a987251bb12b9eab47b39dd2'
PILOT=EXT/'question_free_reconciliation_v20/pilot_attempt01/manifest.json'
PILOT_SHA='95185dfe6bba427335c563512d6697c600b5ea1779162aadbf7d727d1755ddd9'
PILOT_REVIEW=ROOT/'results/question_free_reconciliation_pilot_independent_review_v20.json'
PILOT_REVIEW_SHA='f85e76199fce973df6827a49808cb4a0c7e60e7ebfec2c3512fc7b4fe5e613b7'
sha=lambda b:hashlib.sha256(b).hexdigest()
def require(ok,code):
 if not ok:raise ValueError(code)
def ref(p):return dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p.read_bytes()))
def checked(r):
 p=Path(r['path']);require(ref(p)==r,'artifact_bytes_changed');return p
def load_ref(r):return json.loads(checked(r).read_text())
def pinned(p,h):
 r=ref(p);require(r['sha256']==h,'pinned_artifact_changed');return r,json.loads(p.read_text())
def write(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:f.write(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def module():
 require(ref(ENGINE)['sha256']==ENGINE_SHA,'reviewed_engine_changed')
 sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'src'))
 from temporal_state import question_free_registry_bridge_v20 as bridge
 return bridge

def prepare(args):
 bridge=module();engine_ref=ref(ENGINE);review_ref,_=pinned(REVIEW,REVIEW_SHA)
 initial_ref,initial=pinned(INITIAL,INITIAL_SHA);prep_ref,prep=pinned(PREP,PREP_SHA)
 pilot_ref,pilot=pinned(PILOT,PILOT_SHA);pr_ref,pr=pinned(PILOT_REVIEW,PILOT_REVIEW_SHA)
 require(pr['disagreement_count']==0 and pr['proposal_agreement_count']==54,'pilot_disagreement_requires_new_reviewed_decision_file')
 require(pr['proposal_manifest']==pilot_ref,'pilot_review_parent_changed')
 independent=load_ref(pr['independent_manifest']);checked(independent['private_review']);checked(independent['exposure'])
 decisions=load_ref(pilot['decisions']);checked(pilot['review_record'])
 require(len(decisions)==54 and all(d['review_record_sha256']==pilot['review_record']['sha256'] for d in decisions),'pilot_decision_review_binding')
 require(collections.Counter(d['state'] for d in decisions)=={'supported':27,'unresolved':27},'pilot_state_population')
 require(pilot['bundle_handle']=='p02t32' and pilot['pilot_entry_count']==9,'fixed_pilot_changed')
 by_bundle={r['bundle_handle']:r for r in prep['records']};records=[];keys=[];input_bytes=0
 for r in initial['records']:
  b=r['bundle_handle'];prepared=by_bundle.pop(b);lr=r['initial_ledger'];xr=r['projection'];tr=prepared['typed_bundle']
  ledger=load_ref(lr);projection=load_ref(xr);bundle=load_ref(tr)
  notes=ledger['entries'];occ=projection['occurrences'];registry=bundle['question_free_registry'];parent=bundle['interface_shape_candidate']
  require(len(notes)==len(occ)==len(registry['facts'])==r['entry_count'],'fixed_entry_count_changed')
  local_keys=[dict(bundle_handle=b,occurrence_handle=o['occurrence_handle'],entry_sha256=o['entry_sha256']) for o in occ]
  require([n['key'] for n in notes]==local_keys,'initial_key_order_changed');keys.extend(local_keys)
  selected=decisions if b==pilot['bundle_handle'] else []
  if selected:require({d['key']['occurrence_handle'] for d in selected}=={k['occurrence_handle'] for k in local_keys},'pilot_key_population')
  objects=dict(parent_pack=parent,registry=registry,projection=projection,annotations=ledger,decisions=selected)
  assets={}
  for view in projection['whole_view_availability']:
   for a in [view['pdf'],*view['pages']]:
    ar={k:a[k] for k in ('path','bytes','sha256')};checked(ar);assets[ar['path']]=ar
  card={k:projection['separate_identity_card_reference'][k] for k in ('path','bytes','sha256')};checked(card)
  records.append(dict(bundle_handle=b,entry_count=len(notes),ledger=lr,projection=xr,typed_bundle=tr,decisions=pilot['decisions'] if selected else None,decision_count=len(selected),decision_policy='reviewed_unchanged_pilot' if selected else 'empty_list_all_aspects_pending',input_object_digests={k:bridge.digest(v) for k,v in objects.items()},qualified_view_assets=list(assets.values()),separate_identity_card=card,parent_counts=dict(facts=len(parent['facts']),blocks=len(parent['blocks']),witnesses=len(parent['witnesses']))))
  input_bytes+=sum(x['bytes'] for x in (lr,xr,tr))
 require(not by_bundle and len(records)==21 and len(keys)==326,'fixed_corpus_population')
 require(len({tuple(k.values()) for k in keys})==326,'duplicate_frozen_key')
 out=Path(args.output_directory).resolve();freeze=Path(args.freeze).resolve();public=Path(args.result_receipt).resolve()
 require(not (out/'execution_started.json').exists() and not public.exists(),'output_attempt_already_used')
 manifest=dict(schema_version='question_free_registry_application_freeze_v20',status='frozen_prepared_not_executed',adapter=ref(Path(__file__).resolve()),engine=engine_ref,engine_independent_review=review_ref,engine_contract=ref(ROOT/'docs/question_free_registry_bridge_contract_v20.txt'),reader_dependency=ref(ROOT/'src/temporal_state/reader_binding_v18.py'),initial_freeze=initial_ref,preparation_manifest=prep_ref,pilot_proposal_manifest=pilot_ref,pilot_independent_receipt=pr_ref,pilot_independent_manifest=pr['independent_manifest'],pilot_independent_review=independent['private_review'],pilot_independent_exposure=independent['exposure'],pilot_original_rationale=pilot['review_record'],pilot_unchanged_decisions=pilot['decisions'],expected_key_order=keys,records=records,planned=dict(bundles=21,entries=326,pilot_entries=9,pilot_supported_states=27,pilot_unresolved_states=27,nonpilot_entries=317,nonpilot_pending_states=1902,total_aspect_states=1956),outputs=dict(private_directory=str(out),public_receipt=str(public)),input_artifact_bytes=input_bytes,whole_pack_limits=dict(facts=64,blocks=64,witnesses=512,serialized_bytes=2000000,native_tokens=6144),native_tokenization='not_performed_not_claimed',whole_pack_union_or_candidate_admission=False,root_application_authorization_required=True,source_witness_limit='Frozen occurrence-local text-span variants without offsets may remain unresolved in this consumer. Preserve and count them; do not alter initial ledgers or certify universal witness resolution.',independent_review_is_not_admission=True,author_release_allowed=False,bundle_admitted=False,candidate_admitted=False,candidate_pack_emitted=False)
 write(freeze,manifest)
 receipt=dict(schema_version='question_free_registry_application_preparation_receipt_v20',status=manifest['status'],freeze=ref(freeze),adapter=manifest['adapter'],engine=engine_ref,pilot_independent_receipt=pr_ref,planned=manifest['planned'],whole_pack_limits=manifest['whole_pack_limits'],native_tokenization=manifest['native_tokenization'],reconcile_bundle_calls=0,registry_overlays_emitted=0,author_release_allowed=False,bundle_admitted=False,candidate_admitted=False,candidate_pack_emitted=False,source_witness_limit=manifest['source_witness_limit'])
 write(Path(args.preparation_receipt).resolve(),receipt);print(json.dumps({'freeze':ref(freeze),'status':manifest['status']}))

def apply(args):
 freeze=Path(args.freeze).resolve();fr=ref(freeze);require(fr['sha256']==args.root_authorized_freeze_sha256,'root_authorized_freeze_digest_mismatch')
 manifest=json.loads(freeze.read_text());require(ref(Path(__file__).resolve())==manifest['adapter'],'adapter_changed_after_freeze')
 for field in ('engine','engine_independent_review','engine_contract','reader_dependency','initial_freeze','preparation_manifest','pilot_proposal_manifest','pilot_independent_receipt','pilot_independent_manifest','pilot_independent_review','pilot_independent_exposure','pilot_original_rationale','pilot_unchanged_decisions'):checked(manifest[field])
 bridge=module()
 from temporal_state.reader_binding_v18 import EvidencePack
 out=Path(manifest['outputs']['private_directory']);public=Path(manifest['outputs']['public_receipt'])
 require(not public.exists(),'public_result_already_exists')
 write(out/'execution_started.json',dict(freeze=fr,status='started_after_explicit_root_authorization',author_release_allowed=False))
 records=[];overlays=[];states=collections.Counter();witness_states=collections.Counter();witness_reasons=collections.Counter();joins=collections.Counter();complete=0;error=None
 try:
  for r in manifest['records']:
   for a in r['qualified_view_assets']:checked(a)
   checked(r['separate_identity_card'])
   bundle=load_ref(r['typed_bundle']);projection=load_ref(r['projection']);annotations=load_ref(r['ledger']);decisions=load_ref(r['decisions']) if r['decisions'] else []
   parent=EvidencePack(bundle['interface_shape_candidate'])
   result=bridge.reconcile_bundle(parent,bundle['question_free_registry'],projection,annotations,decisions,expected_digests=r['input_object_digests'],projection_artifact_sha256=r['projection']['sha256'])
   overlay=result['registry_overlay'];sidecar=result['provenance_sidecar']
   for artifact in (overlay,sidecar):require(all(artifact[k] is False for k in ('author_release_allowed','bundle_admitted','candidate_admitted','candidate_pack_emitted')),'unexpected_admission_flag')
   for e in overlay['entries']:
    joins[e['join']['join_status']]+=1;states.update(m['state'] for m in e['aspect_mappings'].values());complete+=int(all(m['state']=='supported' for m in e['aspect_mappings'].values()))
    for m in e['aspect_mappings'].values():
     if m['state']=='supported':require(all(sidecar['witness_checks'][w]['status']=='exact_literal_in_fixed_context' for w in m['source_witness_ids']),'supported_mapping_uses_unresolved_witness')
   for w in sidecar['witness_checks'].values():
    witness_states[w['status']]+=1
    if w['reason'] is not None:witness_reasons[w['reason']]+=1
   op=out/(r['bundle_handle']+'.registry_overlay.json');sp=out/(r['bundle_handle']+'.provenance_sidecar.json');write(op,overlay);write(sp,sidecar)
   overlays.append(overlay);records.append(dict(bundle_handle=r['bundle_handle'],entry_count=r['entry_count'],overlay=ref(op),sidecar=ref(sp)))
  population=bridge.verify_population(overlays,manifest['expected_key_order'])
  require(states=={'supported':27,'unresolved':27,'pending_separate_review':1902},'unexpected_actual_state_population')
  require(complete==0,'unexpected_complete_six_aspect_entry')
 except Exception as exc:
  reason=str(exc);error=dict(kind=type(exc).__name__,code=reason if re.fullmatch('[a-zA-Z0-9_]+',reason or '') else 'application_exception_private_context_suppressed');population=None
 terminal=dict(schema_version='question_free_registry_application_result_v20',status='completed_mechanical_overlay_no_admission' if error is None else 'failed_attempt_preserved',freeze=fr,error=error,completed_bundle_count=len(records),retained_entry_count=sum(r['entry_count'] for r in records),aspect_state_counts=dict(states),join_state_counts=dict(joins),complete_six_aspect_entry_count=complete,witness_check_state_counts=dict(witness_states),unresolved_witness_reason_counts=dict(witness_reasons),universal_witness_validation_claimed=False,population_check=population,records=records,limits=manifest['whole_pack_limits'],native_tokenization='not_performed_not_claimed',candidate_pack_emitted=False,bundle_admitted=False,candidate_admitted=False,author_release_allowed=False,questions_authored=0,reference_answers_authored=0,predictions_authored=0,next_step='Narrow consumer-compatibility amendment before extending substantive reconciliation to occurrence witnesses whose offset-free selector form this frozen consumer does not resolve.')
 write(out/'application_manifest.json',terminal);receipt=dict(terminal);receipt['private_application_manifest']=ref(out/'application_manifest.json');write(public,receipt)
 print(json.dumps({'status':terminal['status'],'public_receipt':ref(public),'aspect_state_counts':dict(states),'unresolved_witness_reason_counts':dict(witness_reasons)}))
 if error is not None:raise SystemExit(1)

def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 f=sub.add_parser('prepare');f.add_argument('--freeze',required=True);f.add_argument('--output-directory',required=True);f.add_argument('--preparation-receipt',required=True);f.add_argument('--result-receipt',required=True)
 a=sub.add_parser('apply');a.add_argument('--freeze',required=True);a.add_argument('--root-authorized-freeze-sha256',required=True)
 args=p.parse_args();(prepare if args.command=='prepare' else apply)(args)
if __name__=='__main__':main()

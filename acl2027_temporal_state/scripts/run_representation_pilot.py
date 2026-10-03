"""Controlled replay over shared v0.3 migrated claims, never held-out accuracy.

All variants receive the same derived claim/alias pack and source prefix. The
legacy projection intentionally loses bounded/observational/negative facts.
Migration is new semantic model work: comparison with v0.2 is NOT extraction-fixed.
"""
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from temporal_state.bounded import build_bounded_memory, DayBounds
from temporal_state.memory import build_memory
from temporal_state.models import Prediction,Question
from temporal_state.pilot_io import load_sources,load_prefix
from temporal_state.representation_io import (load_migration,route_question,
    answer_question,exact_projection,file_sha)

BASE=ROOT/'data/natural_pilot'
DATA=ROOT/'data/natural_pilot_v03'
VARIANTS=('exact_canonical','exact_aliases','bounded_canonical','bounded_aliases',
          'bounded_without_observations','bounded_without_endpoint_bounds')


def read(path):return json.loads(path.read_text())


def run_exact(question,sources,claims,aliases,use_aliases):
    keys=route_question(question.text,claims,aliases,sources,question.information_cutoff,
                        use_aliases=use_aliases)
    if len(keys)!=1:
        return Prediction(question.question_id,'abstain' if not keys else 'indeterminate',
            diagnostics={'reason':'no_predicted_key' if not keys else 'ambiguous_predicted_key',
                         'candidate_keys':keys})
    assertions,excluded=exact_projection(claims)
    memory=build_memory(sources,assertions,question.information_cutoff)
    # Internal routing text is constructed from the system's predicted key, not
    # a gold key. Every variant uses the same router above and unchanged question.
    routed_question=replace(question,text=' '.join(keys[0]))
    prediction=memory.answer(routed_question)
    prediction.diagnostics.update({'routing':'prefix_supported_aliases' if use_aliases else 'canonical_lexical',
       'predicted_key':keys[0],'routing_text_is_internal_predicted_key':True,
       'projection_excluded_negative_claim_ids':excluded,
       'projection_losses':['non_exact_bounds','state_observations','negative_occupancy'],
       'persistence_assumption':memory.diagnostics['open_end_assumption']})
    return prediction


def main():
    loaded=load_sources(BASE/'manifest.json')
    questions=[Question(**row) for row in read(BASE/'questions.json')['questions']]
    predictions=[];migrations=[];actual_controls=[]
    for passid,prefixname in [('a','early'),('b','early'),('l','late')]:
        prefixpath=BASE/f'{prefixname}_prefix_input.json'
        prefix=load_prefix(prefixpath,loaded)
        path=DATA/f'pass_{passid}.json'
        migration=load_migration(path,prefix,BASE/'annotations'/f'pass_{passid}.json',DATA/'migration_prompt.txt')
        migrations.append({'pass_id':passid.upper(),'migration_sha256':file_sha(path),
            'claims':len(migration.claims),'aliases':len(migration.aliases),
            'raw_extraction_sha256':migration.metadata['raw_extraction_sha256'],
            'prefix_input_sha256':prefix.input_sha256})
        for q in questions:
            histories=[h for h,cutoff in prefix.prefixes.items() if cutoff==q.information_cutoff]
            if len(histories)!=1:continue
            history=histories[0]
            sources=[s for s in prefix.sources if prefix.metadata[s.source_id]['history_id']==history]
            ids={s.source_id for s in sources}
            claims=[c for c in migration.claims if c.source_id in ids]
            aliases=[a for a in migration.aliases if a['source_id'] in ids]
            for variant in VARIANTS:
                if variant.startswith('exact_'):
                    prediction=run_exact(q,sources,claims,aliases,variant.endswith('aliases'))
                else:
                    selected=claims
                    if variant=='bounded_without_observations':
                        selected=[replace(c,state_observed_at=()) for c in claims]
                    elif variant=='bounded_without_endpoint_bounds':
                        selected=[replace(c,start=DayBounds(),end=DayBounds()) for c in claims]
                    memory=build_bounded_memory(sources,selected,q.information_cutoff)
                    prediction=answer_question(memory,q,selected,aliases,sources,
                        use_aliases=variant!='bounded_canonical',query_mode='announced_schedule')
                predictions.append({'pass_id':passid.upper(),'history_id':history,'variant':variant,
                    'question':asdict(q),'prediction':asdict(prediction),'migration_sha256':file_sha(path)})
            # Factual-mode smoke outputs deliberately have no schedule gold scores.
            memory=build_bounded_memory(sources,claims,q.information_cutoff)
            actual_controls.append({'pass_id':passid.upper(),'question_id':q.question_id,
                'query_mode':'reported_actual','prediction':asdict(answer_question(memory,q,claims,aliases,sources,query_mode='reported_actual'))})
    predpath=ROOT/'results/representation_v03_predictions.json'
    predpath.write_text(json.dumps({'status':'development_only_shared_migrated_candidates',
        'migrations':migrations,'predictions':predictions,'unscored_actual_mode_controls':actual_controls},indent=2)+'\n')
    # Labels become accessible only after the inference ledger was saved.
    from temporal_state.evaluation import GoldAnswer,evaluate_prediction
    expectations=read(BASE/'development_expectations.json')
    gold={row['gold']['question_id']:GoldAnswer(**row['gold']) for row in expectations['items']}
    scores=[];counts={}
    for row in predictions:
        result=evaluate_prediction(Question(**row['question']),Prediction(**row['prediction']),
                                   gold[row['question']['question_id']],loaded.sources)
        scores.append({'pass_id':row['pass_id'],'variant':row['variant'],**result.to_dict()})
        key=f"{row['pass_id']}:{row['variant']}"
        item=counts.setdefault(key,{'correct':0,'questions':0})
        item['correct']+=int(result.correct);item['questions']+=1
    report={'status':'development_repair_replay_not_paper_result','histories':2,'distinct_questions':9,
       'question_file_sha256':file_sha(BASE/'questions.json'),
       'development_expectations_sha256':file_sha(BASE/'development_expectations.json'),
       'prediction_file_sha256':file_sha(predpath),'variants':list(VARIANTS),'counts':counts,
       'results':scores,'limitations':[
        'No untouched natural test; cases and expectations were previously inspected during development.',
        'Migration adds source-only model interpretation, temporal bounds and aliases. v0.2-v0.3 changes cannot be assigned solely to representation.',
        'Within-v0.3 contrasts share migrated candidates; old exact projection deliberately loses information it cannot encode.',
        'The old exact baseline also assumes forward persistence; the bounded module assumes contiguous selected intervals without forward persistence.',
        'This is a schema/materialization comparison, not joint-versus-independent decoding.',
        'Whole-claim modality cannot represent different modalities for individual endpoints; actual mode has a report-horizon guard.',
        'No joint cross-claim bound propagation, learned shared scores, calibrated confidence or scalable optimizer.',
        'Alias and temporal interpretations are unpinned conversational outputs, not human annotations.',
        'Source availability remains an unverified development convention; text is a current retrieval representation.',
        'No pooled accuracy, bootstrap interval, significance result or model generalization claim.']}
    (ROOT/'results/representation_v03_replay.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'counts':counts},indent=2))


if __name__=='__main__':main()

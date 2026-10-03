"""End-to-end authored controls. No natural accuracy claim is supported here."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from temporal_state.bounded import build_bounded_memory
from temporal_state.models import Question
from temporal_state.pipeline import METHODS, answer_decoded, decode_cache
from temporal_state.representation_io import answer_question
from temporal_state.scored_io import load_cache

DATA=ROOT/'data/coupled_diagnostics'


def main():
    rows=json.loads((DATA/'questions.json').read_text())['questions']
    questions={r['question']['question_id']:Question(**r['question']) for r in rows}
    caches={r['case_id']:load_cache(DATA/'caches'/(r['case_id']+'.json')) for r in rows}
    states={(name,method):decode_cache(cache,method) for name,cache in caches.items() for method in METHODS}
    predictions=[]
    for row in rows:
        name=row['case_id'];cache=caches[name];q=questions[row['question']['question_id']]
        for method in METHODS:
            state=states[name,method]
            prediction=answer_decoded(cache,state,q,query_mode=row['query_mode'])
            unlinked=build_bounded_memory(cache.problem.sources,state.memory.claims,cache.problem.cutoff)
            control=answer_question(unlinked,q,state.memory.claims,cache.aliases,cache.problem.sources,
                                    query_mode=row['query_mode'])
            predictions.append({'case_id':name,'method':method,'query_mode':row['query_mode'],
                'cache_digest':cache.digest,'decoder':asdict(state.result),
                'prediction':asdict(prediction),'same_selection_without_links':asdict(control)})
    ledger={'evidence_level':'authored_integration_controls_only','model_executed':False,
        'cache_hashes':{name:cache.digest for name,cache in caches.items()},'predictions':predictions}
    out=ROOT/'results/coupled_v04_predictions.json'
    out.write_text(json.dumps(ledger,indent=2)+'\n')
    # Labels enter only after every method's predictions are frozen on disk.
    from temporal_state.evaluation import GoldAnswer,evaluate_prediction
    from temporal_state.models import Prediction
    labels=json.loads((DATA/'gold.json').read_text())
    gold={g['question_id']:GoldAnswer(**{**g,
        'accepted_value_sets':tuple(tuple(v) for v in g['accepted_value_sets']),
        'sufficient_evidence_sets':tuple(tuple(v) for v in g['sufficient_evidence_sets'])}) for g in labels['gold']}
    scores=[];controls=[]
    by_question={}
    for row in predictions:
        qid=row['prediction']['question_id'];by_question[qid,row['method']]=row
        if qid in gold:
            scored=evaluate_prediction(questions[qid],Prediction(**row['prediction']),gold[qid],
                                       caches[row['case_id']].problem.sources)
            scores.append({'case_id':row['case_id'],'method':row['method'],**scored.to_dict()})
        else:
            target=next(x for x in labels['unscored_control_expectations'] if x['question_id']==qid)
            controls.append({'question_id':qid,'method':row['method'],
                'passed':row['prediction']['status']==target['status'] and
                         tuple(row['prediction']['values'])==tuple(target['values'])})
    checks=[]
    def record(name,ok):checks.append({'check':name,'passed':bool(ok)})
    for name in caches:
        obj={m:states[name,m].result.objective for m in METHODS}
        record(name+'_shared_objective_order',obj['joint_exact']>=obj['iterative_restarts']>=obj['iterative']>=obj['independent'])
    for name in ('restatement_interval','ordered_unknown_endpoints'):
        record(name+'_coupling_effect_shared_by_all',all(
            by_question[name,m]['prediction']['status']=='answered' and
            by_question[name,m]['same_selection_without_links']['status']=='indeterminate' for m in METHODS))
    record('restarts_match_exact_on_authored_trap',states['coordinated_identity_trap','iterative_restarts'].result.objective==states['coordinated_identity_trap','joint_exact'].result.objective)
    score_map={(s['question_id'],s['method']):s['correct'] for s in scores}
    record('misleading_scores_optimize_wrong_identity',score_map['misleading_pair_scores','independent'] and not score_map['misleading_pair_scores','joint_exact'])
    for name in ('incompatible_restatement','mixed_actual_plan'):
        record(name+'_unsafe_links_rejected_by_all',all(all(lid is None for _,lid in states[name,m].result.link_ids) for m in METHODS))
    record('actual_plan_projection_guard',all(x['passed'] for x in controls))
    report={'evidence_level':'authored_implementation_controls_not_research_accuracy',
        'cases':len(caches),'questions':len(questions),'methods':list(METHODS),
        'prediction_sha256':sha256(out.read_bytes()).hexdigest(),
        'checks':checks,'all_checks_passed':all(x['passed'] for x in checks),'scores':scores,
        'unscored_query_mode_controls':controls,
        'limitations':['Scores and candidates were authored to exercise behavior.',
            'Exhaustive optimization is conditional on one discrete selected state; alternative-state consensus is not checked.',
            'No natural learned-score comparison, held-out result, cost advantage, or algorithmic novelty claim.']}
    (ROOT/'results/coupled_v04_replay.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'cases':len(caches),'method_question_predictions':len(predictions),
        'contract_checks':len(checks),'all_checks_passed':report['all_checks_passed'],
        'status_by_case':{name:{m:by_question[name,m]['prediction']['status'] for m in METHODS} for name in caches}},indent=2))
    if not report['all_checks_passed']:raise SystemExit(1)


if __name__=='__main__':main()

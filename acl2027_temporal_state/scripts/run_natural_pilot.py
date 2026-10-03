"""Replay frozen conversational extraction; this is NOT a model benchmark.

Gold expectations are read only after predictions have been generated. The
v0.1 memory's exact-date-only limitation is retained rather than silently
turning observation dates into appointments. No extractor API is called.
"""
from pathlib import Path
from dataclasses import asdict
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from temporal_state.models import Assertion, Question
from temporal_state.memory import build_memory

DATA=ROOT/'data/natural_pilot'

def read(path):
    return json.loads(path.read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def project_assertions(extraction):
    """Lossy, explicit projection of source extraction to the old memory API.

    Negative statements do not assert positive occupancy. Observation dates and
    uncertain starts are never promoted to exact effective starts. Discourse
    links are left in the raw ledger: this baseline receives ASSERT or UNRESOLVED
    because its target API cannot encode multiple alternative readings. Planned
    positives are retained for the source-conditional announced-schedule queries.
    """
    projected=[]; excluded=[]
    for row in extraction['assertions']:
        # The v0.2 prompt's single status enum conflates modality and polarity.
        # Pass B explicitly put future negation in the qualifier. Preserve that
        # machine-written marker, never project it as positive occupancy.
        negative_qualifier=str(row.get('qualifier','')).casefold().startswith('negative assertion:')
        if row['status']=='negative' or negative_qualifier:
            excluded.append({'assertion_id':row['assertion_id'],'reason':'negative_state_not_supported_by_v0_1_memory'})
            continue
        if not isinstance(row.get('value'),str) or not row['value'].strip():
            excluded.append({'assertion_id':row['assertion_id'],'reason':'non_scalar_or_empty_value_not_supported'})
            continue
        op='UNRESOLVED' if row['status']=='uncertain' or row['operation']=='UNRESOLVED' else 'ASSERT'
        projected.append(Assertion(row['assertion_id'],row['source_id'],row['subject'],row['relation'],row['value'],row['valid_from'],row['valid_to'],row['scope'],op,None,row['evidence'][0]['quote'],f"frozen_conversational_pass_{extraction['pass_id']}",tuple(row['context_source_ids'])))
    return projected,excluded

def main():
    from temporal_state.pilot_io import load_sources,load_prefix,validate_extraction
    loaded=load_sources(DATA/'manifest.json')
    packs={name:load_prefix(DATA/f'{name}_prefix_input.json',loaded) for name in ['early','late']}
    # The questions file contains questions only; labels cannot enter inference.
    questions=[Question(**q) for q in read(DATA/'questions.json')['questions']]
    predictions=[]
    for passid,packname in [('a','early'),('b','early'),('l','late')]:
        path=DATA/'annotations'/f'pass_{passid}.json'
        extraction=validate_extraction(path,packs[packname])
        assertions,excluded=project_assertions(extraction)
        # Use the extraction input's own source IDs, not all later sources.
        rawpack=read(DATA/f'{packname}_prefix_input.json')
        sourceids={s['source_id'] for s in rawpack['sources']}
        sources=[s for s in loaded.sources if s.source_id in sourceids]
        source_history={s['source_id']:s['history_id'] for s in rawpack['sources']}
        for q in questions:
            matched=[h for h,cutoff in rawpack['prefixes'].items() if cutoff==q.information_cutoff]
            if len(matched)!=1:continue
            history=matched[0]
            hs=[s for s in sources if source_history[s.source_id]==history]
            ha=[a for a in assertions if source_history[a.source_id]==history]
            mem=build_memory(hs,ha,q.information_cutoff)
            for policy in ['episodes','newest_available']:
                prediction=mem.answer(q,policy)
                predictions.append({'pass_id':passid.upper(),'question':asdict(q),'history_id':history,
                    'policy':policy,'prediction':asdict(prediction),'projection_exclusions':excluded,
                    'memory_diagnostics':mem.diagnostics,'extraction_sha256':sha(path)})
    # Prediction ledger freezes before gold-only evaluation modules and labels.
    predpath=ROOT/'results/natural_pilot_predictions.json'
    predpath.write_text(json.dumps({'evidence_level':'development_replay_of_unpinned_conversational_extraction','predictions':predictions},indent=2)+'\n')
    from temporal_state.evaluation import GoldAnswer,evaluate_prediction
    from temporal_state.models import Prediction
    expectations=read(DATA/'development_expectations.json')
    gold={x['question']['question_id']:GoldAnswer(**{**x['gold'],
        'accepted_value_sets':tuple(tuple(v) for v in x['gold']['accepted_value_sets']),
        'sufficient_evidence_sets':tuple(tuple(v) for v in x['gold']['sufficient_evidence_sets'])}) for x in expectations['items']}
    scored=[]
    for row in predictions:
        result=evaluate_prediction(Question(**row['question']),Prediction(**row['prediction']),gold[row['question']['question_id']],loaded.sources)
        scored.append({'pass_id':row['pass_id'],'policy':row['policy'],**result.to_dict()})
    counts={}
    for row in scored:
        key=f"{row['pass_id']}:{row['policy']}"
        item=counts.setdefault(key,{'correct':0,'questions':0})
        item['correct']+=int(row['correct']);item['questions']+=1
    report={'status':'development_diagnostic_not_publishable_benchmark_result','independent_histories':2,
        'expectations_sha256':sha(DATA/'development_expectations.json'),'prediction_file_sha256':sha(predpath),
        'counts':counts,'results':scored,'limitations':[
          'A/B are same-family conversational passes, not independently pinned model families or human raters.',
          'L independently re-extracted a later prefix; cross-prefix differences can include extractor variation.',
          'Source availability follows a development convention, not verified historical availability.',
          'This replays stored extraction outputs; it does not measure reproducible end-to-end model latency or cost.',
          'Exact-date v0.1 memory cannot faithfully express observation-only or bounded starts; dates are not invented.',
          'Lexical routing can fail on legal-name aliases; no gold names are injected into routing.',
          'Projection removes negative claims and discourse targets; resulting omissions are recorded.',
          'No joint-vs-baseline natural comparison, generalization estimate, confidence interval, or significance claim.']}
    (ROOT/'results/natural_pilot_replay.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'counts':counts},indent=2))

if __name__=='__main__':main()

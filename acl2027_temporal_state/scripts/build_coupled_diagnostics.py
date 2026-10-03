"""Author deterministic integration fixtures. These are not natural evidence.

This builder never reads evaluation labels or questions. Wrong alternatives and
misleading scores are deliberately retained to separate optimization from truth.
"""
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.decoder import Link, Mention, Problem, Reading
from temporal_state.models import Source
from temporal_state.scored_io import dump_cache, make_cache

SCORES = 'hand_authored_integration_logits; not_model_scores'
CUTOFF = '2024-01-31'


def source(sid, text):
    return Source(sid, text, CUTOFF, availability_basis='authored_fixture', provenance='synthetic')


def claim(mid, rid, src, subject, value, *, start=(None,None), end=(None,None),
          obs=(), modality='reported_actual', reported=CUTOFF):
    return TemporalClaim(mid+'_'+rid, src.source_id, subject, 'CEO', value,
        reported_at=reported, start=DayBounds(*start), end=DayBounds(*end),
        state_observed_at=tuple(obs), modality=modality,
        evidence_spans=(EvidenceSpan(0,len(src.text),src.text),),
        derivation_note='Hand-authored candidate, including deliberately wrong alternatives.')


def cache(rows, sources, links=()):
    mentions, bindings = [], {}
    for mid, choices in rows:
        readings=[]
        for rid,c,score in choices:
            bindings[mid,rid]=c
            readings.append(Reading(rid,c.key,c.value,score,
                c.start.lower if c.start.lower==c.start.upper else None,
                c.end.lower if c.end.lower==c.end.upper else None,
                min(c.state_observed_at) if c.state_observed_at else None))
        readings.append(Reading('unknown',None,None,-10))
        c=choices[0][1]; src=next(s for s in sources if s.source_id==c.source_id)
        mentions.append(Mention(mid,src.source_id,0,len(src.text),tuple(readings)))
    problem=Problem(CUTOFF,tuple(sources),tuple(mentions),tuple(links),SCORES)
    provenance=dict(evidence_type='authored_diagnostic',candidate_model_id=None,
        candidate_model_revision=None,scorer_model_id=None,scorer_model_revision=None,
        prompt_sha256=None,config_sha256=sha256(b'v04-seven-authored-cases-2026-10-02').hexdigest(),
        score_definition=SCORES)
    return make_cache(problem,bindings,(),provenance)


def fixtures():
    out={}
    a=source('a','Acme reported that Alice was its CEO from January 1 through January 30, 2024.')
    c=claim('a','r',a,'Acme','Alice',start=('2024-01-01',)*2,end=('2024-01-31',)*2)
    out['clear_no_interaction']=cache([('a',[('r',c,1)])],[a])

    a=source('a','Alice was Acme CEO on January 10, 2024.')
    b=source('b','Alice remained Acme CEO on January 20, 2024, in the same tenure.')
    ca=claim('a','r',a,'Acme','Alice',obs=('2024-01-10',))
    cb=claim('b','r',b,'Acme','Alice',obs=('2024-01-20',))
    out['restatement_interval']=cache([('a',[('r',ca,1)]),('b',[('r',cb,1)])],[a,b],
        [Link('same_episode','b','r','a','r','RESTATES',2)])

    b=source('b','Bob was Acme CEO on January 20, 2024, after Alice left the role.')
    cb=claim('b','r',b,'Acme','Bob',obs=('2024-01-20',))
    out['ordered_unknown_endpoints']=cache([('a',[('r',ca,1)]),('b',[('r',cb,1)])],[a,b],
        [Link('successor','b','r','a','r','CHANGES',2)])

    for name,true_subject in [('coordinated_identity_trap','Atlas Subsidiary'),
                              ('misleading_pair_scores','Atlas Parent')]:
        a=source('a',f'Alice was CEO of {true_subject} from January 1 to January 15, 2024.')
        b=source('b',f'Bob became CEO of {true_subject} on January 15 and held the role through January 30, 2024.')
        rows=[]
        for mid,src,value,start,end in [('a',a,'Alice','2024-01-01','2024-01-15'),
                                        ('b',b,'Bob','2024-01-15','2024-01-31')]:
            choices=[]
            for rid,subject,score in [('parent','Atlas Parent',1),('subsidiary','Atlas Subsidiary',0)]:
                c=claim(mid,rid,src,subject,value,start=(start,)*2,end=(end,)*2)
                choices.append((rid,c,score))
            rows.append((mid,choices))
        out[name]=cache(rows,[a,b],[Link('pair','b','subsidiary','a','subsidiary','CHANGES',3)])

    a=source('a','Alice was Acme CEO from January 1 until an unspecified day between January 10 and January 12, 2024.')
    b=source('b','A later Alice tenure as Acme CEO began between January 20 and January 22, 2024, and ended January 31.')
    ca=claim('a','r',a,'Acme','Alice',start=('2024-01-01',)*2,end=('2024-01-10','2024-01-12'))
    cb=claim('b','r',b,'Acme','Alice',start=('2024-01-20','2024-01-22'),end=('2024-01-31',)*2)
    out['incompatible_restatement']=cache([('a',[('r',ca,1)]),('b',[('r',cb,1)])],[a,b],
        [Link('wrong_merge','b','r','a','r','RESTATES',10)])

    a=source('a','On January 10, 2024, Acme reported Alice was its CEO that day.')
    b=source('b','On January 10, 2024, Acme announced a plan for Alice to serve as CEO from January 15 through January 30.')
    ca=claim('a','r',a,'Acme','Alice',obs=('2024-01-10',),reported='2024-01-10')
    cb=claim('b','r',b,'Acme','Alice',start=('2024-01-15',)*2,end=('2024-01-31',)*2,
             modality='announced_future',reported='2024-01-10')
    out['mixed_actual_plan']=cache([('a',[('r',ca,1)]),('b',[('r',cb,1)])],[a,b],
        [Link('unsafe_plan_merge','b','r','a','r','RESTATES',10)])
    return out


if __name__=='__main__':
    folder=ROOT/'data/coupled_diagnostics/caches';folder.mkdir(parents=True,exist_ok=True)
    records=fixtures()
    for name,item in records.items():dump_cache(item,folder/(name+'.json'))
    print(f'Wrote {len(records)} authored caches. No model or evaluation labels used.')

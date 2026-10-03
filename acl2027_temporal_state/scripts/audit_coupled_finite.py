"""Independent seeded finite-domain audit of conditional temporal closure."""
import itertools, random, sys, json
from pathlib import Path
from datetime import date
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from temporal_state.bounded import DayBounds, EvidenceSpan, TemporalClaim
from temporal_state.coupled import materialize_selection, TemporalInfeasible
from temporal_state.decoder import Problem, Mention, Reading, Link
from temporal_state.models import Source

rng=random.Random(20261002)
def day(n): return f'2024-01-{n:02d}'
def ordinal(n): return date.fromisoformat(day(n)).toordinal()
checks=0; feasible=0; nonfeasible=0; model_count=0
for case in range(500):
    n=rng.choice([2,3]); sources=[]; mentions=[]; claims={}; domains=[]
    for i in range(n):
        mid=f'm{i}'; sid=f's{i}'; sources.append(Source(sid,'A source-supported claim.',day(31)))
        while True:
            sl,su=sorted(rng.sample(range(1,6),2)); el,eu=sorted(rng.sample(range(1,6),2))
            states=[(s,e) for s in range(sl,su+1) for e in range(el,eu+1) if s<e]
            if states: break
        observed=()
        if rng.random()<.35:
            pair=rng.choice(states); obs=rng.randrange(*pair); observed=(day(obs),)
            states=[(s,e) for s,e in states if s<=obs<e]
        value=rng.choice(['Alice','Bob']); polarity=rng.choice(['positive','positive','negative']); modality=rng.choice(['reported_actual','reported_actual','announced_future'])
        if modality=='announced_future': observed=(); states=[(s,e) for s in range(sl,su+1) for e in range(el,eu+1) if s<e]
        claim=TemporalClaim(f'c{i}',sid,'org','ceo',value,polarity=polarity,modality=modality,reported_at=day(31),start=DayBounds(day(sl),day(su)),end=DayBounds(day(el),day(eu)),state_observed_at=observed,evidence_spans=(EvidenceSpan(0,1,'A'),))
        claims[mid,'r']=claim; domains.append(states)
        reading=Reading('r',('org','ceo',''),value,1,observed_at=observed[0] if observed else None)
        mentions.append(Mention(mid,sid,0,1,(reading,Reading('u',None,None,0))))
    links=[]; lids={f'm{i}':None for i in range(n)}
    for i in range(n):
        if rng.random()<.7:
            j=rng.choice([j for j in range(n) if j!=i]); relation=rng.choice(['RESTATES','CHANGES'])
            lid=f'l{i}'; links.append(Link(lid,f'm{i}','r',f'm{j}','r',relation,1)); lids[f'm{i}']=lid
    problem=Problem(day(31),tuple(sources),tuple(mentions),tuple(links),'independent authored finite-domain audit')
    possible=[]
    for state in itertools.product(*domains):
        valid=True
        for link in links:
            i=int(link.mention_id[1:]);j=int(link.target_mention_id[1:]); a=claims[f'm{i}','r'];b=claims[f'm{j}','r']
            if a.modality!=b.modality: valid=False;break
            if link.relation=='RESTATES':
                if a.value!=b.value or a.polarity!=b.polarity or state[i]!=state[j]: valid=False;break
            else:
                if a.value==b.value or a.polarity!='positive' or b.polarity!='positive' or state[j][1]>state[i][0]: valid=False;break
        if valid: possible.append(state)
    try: memory=materialize_selection(problem,{f'm{i}':'r' for i in range(n)},lids,claims)
    except TemporalInfeasible:
        assert not possible, (case,'false infeasible',possible)
        nonfeasible+=1;checks+=1;continue
    assert possible,(case,'false feasible')
    feasible+=1; model_count+=len(possible);checks+=1
    for i in range(n):
        env=memory.envelopes[f'c{i}']
        observed=(env.start_lower,env.start_upper,env.end_lower,env.end_upper)
        expected=(ordinal(min(s[i][0] for s in possible)),ordinal(max(s[i][0] for s in possible)),ordinal(min(s[i][1] for s in possible)),ordinal(max(s[i][1] for s in possible)))
        assert observed==expected,(case,i,'envelope',observed,expected);checks+=1
        for t in range(1,6):
            membership=env.membership(day(t)); truth=[s[i][0]<=t<s[i][1] for s in possible]
            assert (membership.may,membership.must)==(any(truth),all(truth)),(case,i,t,'membership')
            checks+=1
report={'random_seed':20261002,'networks':500,'feasible_networks':feasible,'infeasible_networks':nonfeasible,'explicit_feasible_assignments':model_count,'assertion_checks':checks,'status':'all passed','evidence_type':'independent_finite_generated_audit_not_natural_accuracy'}
(ROOT/'results/coupled_finite_audit_v04.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
